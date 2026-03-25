#!/usr/bin/env python3
"""
MCP Memory Server — memory CRUD tools, JSON-RPC protocol compliant.
Owns all Cosmos DB CRUD and embedding operations.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import AzureOpenAI

app = FastAPI(title="Memory MCP Server")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mcp-memory")

# Suppress verbose Azure SDK HTTP logging
logging.getLogger("azure").setLevel(logging.WARNING)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT")
FOUNDRY_EMBEDDING_DEPLOYMENT = os.getenv("FOUNDRY_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
FOUNDRY_MEMORY_MODEL_DEPLOYMENT = os.getenv("FOUNDRY_MEMORY_MODEL_DEPLOYMENT", "gpt-5-mini")
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE", "agentmemory")
COSMOS_CONTAINER = os.getenv("COSMOS_CONTAINER", "memories")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "3072"))

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(
    credential, "https://cognitiveservices.azure.com/.default"
)

openai_client = AzureOpenAI(
    api_version="2024-10-21",
    azure_endpoint=FOUNDRY_ENDPOINT,
    azure_ad_token_provider=token_provider,
)

cosmos_client = CosmosClient(url=COSMOS_ENDPOINT, credential=credential) if COSMOS_ENDPOINT else None
cosmos_container = (
    cosmos_client.get_database_client(COSMOS_DATABASE).get_container_client(COSMOS_CONTAINER)
    if cosmos_client
    else None
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed(text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    resp = openai_client.embeddings.create(
        input=text,
        model=FOUNDRY_EMBEDDING_DEPLOYMENT,
        dimensions=EMBEDDING_DIMENSIONS,
    )
    return resp.data[0].embedding


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def execute_save_memory(args: dict) -> dict:
    """Embed, deduplicate, and upsert a memory into Cosmos DB."""
    if not cosmos_container:
        return {"error": "Cosmos DB not configured"}

    user_id = args["user_id"]
    content = args["content"]
    category = args["category"]
    tags = args.get("tags", [])
    source_conversation_id = args.get("source_conversation_id", "")

    embedding = _embed(content)

    # Dedup: vector search for near-duplicates from the same user
    dupes = _vector_search(user_id, embedding, top_k=3, score_threshold=0.95)
    if dupes:
        # Update existing memory instead of creating a duplicate
        existing = dupes[0]
        existing["content"] = content
        existing["tags"] = list(set(existing.get("tags", []) + tags))
        existing["updated_at"] = _now_iso()
        existing["embedding"] = embedding
        cosmos_container.upsert_item(existing)
        logger.info(f"MEMORY UPDATED [{category}] \"{content[:80]}\" (score={dupes[0].get('score', 0):.3f}, id={existing['id']})")
        return {"id": existing["id"], "action": "updated", "matched_score": dupes[0].get("score")}

    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "category": category,
        "content": content,
        "tags": tags,
        "embedding": embedding,
        "source_conversation_id": source_conversation_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    cosmos_container.upsert_item(doc)
    logger.info(f"MEMORY CREATED [{category}] \"{content[:80]}\" (id={doc['id']})")
    return {"id": doc["id"], "action": "created"}


def _vector_search(user_id: str, embedding: list[float], top_k: int = 10, score_threshold: float = 0.0) -> list[dict]:
    """Run a Cosmos DB vector search scoped to a user."""
    if not cosmos_container:
        return []

    query = (
        "SELECT TOP @top_k c.id, c.user_id, c.category, c.content, c.tags, "
        "c.created_at, c.updated_at, c.source_conversation_id, "
        "VectorDistance(c.embedding, @embedding) AS score "
        "FROM c WHERE c.user_id = @user_id "
        "ORDER BY VectorDistance(c.embedding, @embedding)"
    )
    params = [
        {"name": "@top_k", "value": top_k},
        {"name": "@embedding", "value": embedding},
        {"name": "@user_id", "value": user_id},
    ]
    results = list(cosmos_container.query_items(
        query=query,
        parameters=params,
        partition_key=user_id,
    ))
    if score_threshold > 0:
        results = [r for r in results if r.get("score", 0) >= score_threshold]
    return results


def execute_search_memories(args: dict) -> dict:
    """Semantic search over a user's memories."""
    user_id = args["user_id"]
    query_text = args["query"]
    top_k = args.get("top_k", 10)
    category = args.get("category")

    embedding = _embed(query_text)
    results = _vector_search(user_id, embedding, top_k=top_k)

    if category:
        results = [r for r in results if r.get("category") == category]

    # Strip embedding from response
    for r in results:
        r.pop("embedding", None)

    return {"memories": results}


def execute_get_user_profile(args: dict) -> dict:
    """Fetch identity + preference memories for a user (no embedding needed)."""
    if not cosmos_container:
        return {"error": "Cosmos DB not configured"}

    user_id = args["user_id"]
    query = (
        "SELECT c.id, c.category, c.content, c.tags, c.created_at, c.updated_at "
        "FROM c WHERE c.user_id = @user_id AND c.category IN ('identity', 'preference') "
        "ORDER BY c.updated_at DESC"
    )
    params = [{"name": "@user_id", "value": user_id}]
    results = list(cosmos_container.query_items(
        query=query,
        parameters=params,
        partition_key=user_id,
    ))
    return {"profile": results}


# ---------------------------------------------------------------------------
# Memory extraction (the memory service owns the full pipeline)
# ---------------------------------------------------------------------------

MEMORY_EXTRACTION_PROMPT = """Analyze the following conversation and extract any memorable facts about the user.
Only extract information that would be useful to remember for future conversations.
Do NOT extract trivial or transient information (e.g. "user said hello").
Write each memory in third person about the user. Each memory should be a single atomic fact.

Also extract the main topics or questions the user discussed during the conversation.
For each distinct topic or question, create an `episode` memory summarizing what was discussed.

Categorize each memory using exactly one of these types:
- identity: who the user is — name, role, job title, location, demographics.
  Examples: "The user's name is Alice.", "The user is a DevOps engineer at Contoso."
- preference: likes, dislikes, style choices, ways of working.
  Examples: "The user prefers dark mode.", "The user dislikes verbose explanations."
- knowledge: facts the user knows, expertise, skill level, domain knowledge.
  Examples: "The user is proficient in Rust.", "The user has experience with Kubernetes."
- goal: objectives, plans, aspirations, things the user wants to achieve.
  Examples: "The user is preparing for a marathon.", "The user wants to learn Japanese."
- context: current situation, active project, tools in use, temporary circumstances.
  Examples: "The user is working on a migration project.", "The user is using VS Code on Linux."
- relationship: connections to people, teams, organizations the user mentions.
  Examples: "The user's manager is Sarah.", "The user collaborates with the platform team."
- episode: summary of a notable past interaction, event, or conversation topic.
  Examples: "The user asked about the PTO policy.", "The user discussed how to set up out-of-office replies.", \
"The user resolved a production outage last week.", "The user demoed the app to leadership."
- sentiment: expressed feelings, attitudes, or emotions about specific topics.
  Examples: "The user is enthusiastic about AI tooling.", "The user is frustrated with slow CI pipelines."

Conversation:
{transcript}"""

MEMORY_EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "memory_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "required": ["memories"],
            "additionalProperties": False,
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["category", "content", "tags"],
                        "additionalProperties": False,
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": [
                                    "identity", "preference", "knowledge", "goal",
                                    "context", "relationship", "episode", "sentiment",
                                ],
                            },
                            "content": {
                                "type": "string",
                                "description": "A concise third-person statement about the user.",
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Lowercase keywords for filtering.",
                            },
                        },
                    },
                }
            },
        },
    },
}


def execute_store_memories(args: dict) -> dict:
    """Extract memories from a conversation transcript via LLM, then save each one."""
    user_id = args["user_id"]
    conversation_id = args.get("conversation_id", "")
    messages = args["messages"]  # list of {role, content}

    # Build transcript from user/assistant messages only
    transcript_lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            transcript_lines.append(f"{role}: {content}")

    if len(transcript_lines) < 2:
        return {"memories_saved": 0, "reason": "Not enough conversation to extract from"}

    transcript = "\n".join(transcript_lines)

    response = openai_client.chat.completions.create(
        model=FOUNDRY_MEMORY_MODEL_DEPLOYMENT,
        messages=[
            {"role": "system", "content": "You extract memorable facts from conversations."},
            {"role": "user", "content": MEMORY_EXTRACTION_PROMPT.format(transcript=transcript)},
        ],
        response_format=MEMORY_EXTRACTION_SCHEMA,
    )

    try:
        result = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, IndexError, AttributeError) as e:
        logger.error(f"Failed to parse LLM extraction response: {e}")
        return {"memories_saved": 0, "reason": f"LLM response parse error: {e}"}
    memories = result.get("memories", [])

    saved = []
    for mem in memories:
        save_result = execute_save_memory({
            "user_id": user_id,
            "category": mem["category"],
            "content": mem["content"],
            "tags": mem.get("tags", []),
            "source_conversation_id": conversation_id,
        })
        saved.append({"content": mem["content"][:80], "category": mem["category"], **save_result})

    return {"memories_saved": len(saved), "details": saved}


# ---------------------------------------------------------------------------
# Tool definitions (MCP schema)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_memories",
        "description": "Semantic search over a user's memories. Returns the most relevant memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier"},
                "query": {"type": "string", "description": "Search query text"},
                "top_k": {"type": "integer", "description": "Max results (default 10)"},
                "category": {"type": "string", "description": "Optional category filter"},
            },
            "required": ["user_id", "query"],
        },
    },
    {
        "name": "get_user_profile",
        "description": "Fetch identity and preference memories for a user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "store_memories",
        "description": "Extract and save memories from a conversation. Analyzes the transcript with an LLM, extracts memorable facts, and saves each one (with deduplication).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier"},
                "conversation_id": {"type": "string", "description": "Conversation ID for provenance tracking"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                    "description": "Conversation messages (role + content)",
                },
            },
            "required": ["user_id", "messages"],
        },
    },
]

# Map tool names to handler functions
TOOL_HANDLERS = {
    "search_memories": execute_search_memories,
    "get_user_profile": execute_get_user_profile,
    "store_memories": execute_store_memories,
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"name": "memory-mcp-server", "version": "2.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/mcp")
async def mcp_jsonrpc(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({
            "jsonrpc": "2.0",
            "error": {"code": -32700, "message": f"Parse error: {e}"},
            "id": None,
        })

    logger.info(f"MCP Request: method={body.get('method', '')} tool={body.get('params', {}).get('name', '')}")

    jsonrpc = body.get("jsonrpc", "2.0")
    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id")

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": jsonrpc,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memory-mcp-server", "version": "2.0.0"},
            },
            "id": request_id,
        })

    if method == "notifications/initialized":
        return JSONResponse({"jsonrpc": jsonrpc, "result": {}, "id": request_id})

    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": jsonrpc,
            "result": {"tools": TOOLS},
            "id": request_id,
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return JSONResponse({
                "jsonrpc": jsonrpc,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                "id": request_id,
            })

        try:
            result = handler(arguments)
            return JSONResponse({
                "jsonrpc": jsonrpc,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}]
                },
                "id": request_id,
            })
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return JSONResponse({
                "jsonrpc": jsonrpc,
                "error": {"code": -32000, "message": f"Tool execution failed: {e}"},
                "id": request_id,
            })

    return JSONResponse({
        "jsonrpc": jsonrpc,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
        "id": request_id,
    })


@app.get("/mcp/tools")
async def list_tools():
    return {"tools": TOOLS}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
