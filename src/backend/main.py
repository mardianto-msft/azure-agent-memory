"""
Backend API for Agent Memory — AI Agent powered by Microsoft Foundry.
Uses azure-ai-projects to create an AI agent with function tools
for memory retrieval, memory search, and knowledge base search.
The agent decides when to call each tool based on the conversation context.
"""

import asyncio
import json
import logging
import os
import time

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backend")

# Suppress verbose Azure SDK HTTP logging
logging.getLogger("azure").setLevel(logging.WARNING)

app = FastAPI(title="Agent Memory API", version="3.0.0")

# CORS configuration
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
FOUNDRY_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
FOUNDRY_CHAT_MODEL_DEPLOYMENT = os.getenv("FOUNDRY_CHAT_MODEL_DEPLOYMENT", "gpt-5.1")
MCP_MEMORY_ENDPOINT = os.getenv("MCP_MEMORY_ENDPOINT")
MCP_SEARCH_ENDPOINT = os.getenv("MCP_SEARCH_ENDPOINT")

AGENT_NAME = "agent-memory-assistant"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(
    credential, "https://cognitiveservices.azure.com/.default"
)

# ---------------------------------------------------------------------------
# AI Project client, OpenAI project client, and Agent (initialized at startup)
# ---------------------------------------------------------------------------
project_client = None
openai_project_client = None
agent = None

# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------
AGENT_INSTRUCTIONS = """\
You are a helpful AI assistant with access to the user's memories and a knowledge base.

You have the following tools:
- search_memories: Semantically searches the user's saved memories. \
Use when the topic might relate to something discussed previously.
- search_knowledge: Searches the knowledge base documentation. \
Use when the user asks questions that might be answered by indexed documentation.

STRICT SCOPE — READ CAREFULLY:
You are a RETRIEVAL-ONLY assistant. You must NEVER generate answers from your own \
training data or general knowledge. Every factual claim in your response must come \
from one of these sources:
1. Results returned by search_knowledge or search_memories.
2. The user's profile context provided at the start of the conversation.
3. Information the user explicitly stated in the current conversation.

If a question cannot be answered from those sources, you MUST refuse. Say something like: \
"I don't have information on that topic in the knowledge base or your memories." \
Do NOT attempt to be helpful by answering from general knowledge.

Examples of questions you must REFUSE (unless found in tools/context):
- Weather, traffic, sports scores, news, stock prices, or any real-time information.
- General knowledge questions (e.g. "What is the capital of France?").
- Coding help, math problems, or trivia unrelated to the knowledge base.
- Recommendations (restaurants, movies, products) not in the knowledge base.

WHEN TO USE TOOLS:
- Use search_memories whenever the user asks about something that could relate to \
a previous conversation — topics they discussed, questions they asked, things they \
mentioned, or any reference to the past. Do not wait for explicit phrases like \
"last time" or "remember when". If there is any chance the answer is in their \
memory history, search first.
- Use search_knowledge when the user's question might be answered by indexed documentation.
- If unsure whether a topic is covered, call the tool first. If it returns no relevant \
results, refuse — do not fall back to your own knowledge.

MEMORY SYSTEM:
- Memories are AUTOMATICALLY extracted from every conversation in the background. \
You do not need to do anything to save them.
- When a user shares personal information (name, role, preferences, etc.), acknowledge \
it naturally and let them know you'll remember it in future conversations.
- If the user's profile is provided at the start and includes their name, greet them \
by name on the first message of the conversation.
- Do NOT say you cannot store or save memories. The system handles this automatically.
- On subsequent conversations, the user's profile (identity and preferences) is \
automatically loaded and provided to you at the start. You do not need to fetch it.

RESPONSE GUIDELINES:
- Keep responses clear and concise.
- When citing knowledge base results, include the source as a markdown hyperlink \
with descriptive text, e.g. [How to set up out-of-office replies](https://...). \
Never show bare URLs.
- You may engage in brief pleasantries (greetings, thanks) but must not answer \
substantive questions outside your tool-backed scope."""

# ---------------------------------------------------------------------------
# Function tool definitions
# ---------------------------------------------------------------------------
AGENT_TOOLS = [
    FunctionTool(
        name="search_memories",
        description=(
            "Semantically search the user's saved memories for information "
            "relevant to the current topic."
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "The user's unique identifier.",
                },
                "query": {
                    "type": "string",
                    "description": "The search query in natural language.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return.",
                },
            },
            "required": ["user_id", "query", "top_k"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="search_knowledge",
        description=(
            "Search the knowledge base documentation using hybrid search "
            "(text + vector + semantic reranking)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query in natural language.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return.",
                },
            },
            "required": ["query", "top_k"],
            "additionalProperties": False,
        },
        strict=True,
    ),
]


# ---------------------------------------------------------------------------
# Startup — create the AI agent (with retries for RBAC propagation)
# ---------------------------------------------------------------------------

MAX_STARTUP_RETRIES = 5
STARTUP_RETRY_DELAY = 10  # seconds


def _init_agent():
    """Attempt to initialize the project client and agent. Returns True on success."""
    global project_client, openai_project_client, agent

    project_client = AIProjectClient(
        endpoint=FOUNDRY_PROJECT_ENDPOINT,
        credential=credential,
    )
    openai_project_client = project_client.get_openai_client()

    # Always create a new version to pick up instruction/tool changes
    agent = project_client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=FOUNDRY_CHAT_MODEL_DEPLOYMENT,
            instructions=AGENT_INSTRUCTIONS,
            tools=AGENT_TOOLS,
        ),
    )
    logger.info(f"Agent created: {agent.name} v{agent.version}")
    return True


@app.on_event("startup")
def startup_create_agent():
    if not FOUNDRY_PROJECT_ENDPOINT:
        logger.warning("FOUNDRY_PROJECT_ENDPOINT not set — agent features disabled")
        return

    for attempt in range(1, MAX_STARTUP_RETRIES + 1):
        try:
            _init_agent()
            return
        except Exception as e:
            logger.warning(
                f"Agent init attempt {attempt}/{MAX_STARTUP_RETRIES} failed: {e}"
            )
            if attempt < MAX_STARTUP_RETRIES:
                logger.info(f"Retrying in {STARTUP_RETRY_DELAY}s (RBAC may still be propagating)...")
                time.sleep(STARTUP_RETRY_DELAY)

    logger.error("Agent initialization failed after all retries")


# Request models
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatStreamRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    thread_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    thread_id: Optional[str] = None


class StoreMemoriesRequest(BaseModel):
    user_id: str
    conversation_id: str
    messages: List[ChatMessage]
    wait: bool = False


# ---------------------------------------------------------------------------
# Sync MCP tool execution (used by the agent loop, runs in a thread)
# ---------------------------------------------------------------------------

def _call_mcp_tool_sync(tool_name: str, arguments: dict, endpoint: str) -> dict:
    """Call an MCP tool via JSON-RPC (synchronous)."""
    mcp_url = f"{endpoint.rstrip('/')}/mcp"
    jsonrpc_request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": 1,
    }

    logger.debug(f"MCP CALL -> {mcp_url} tool={tool_name} args={arguments}")

    with httpx.Client(timeout=60.0) as client:
        response = client.post(mcp_url, json=jsonrpc_request)

    logger.debug(f"MCP RESPONSE <- status={response.status_code} body={response.text[:500]}")

    if response.status_code != 200:
        logger.error(f"MCP server returned {response.status_code}: {response.text[:500]}")
        return {"error": f"MCP server returned {response.status_code}"}

    data = response.json()
    if "error" in data:
        logger.error(f"MCP error: {data['error']}")
        return {"error": data["error"].get("message", "Unknown MCP error")}

    content = data.get("result", {}).get("content", [])
    if content and "text" in content[0]:
        try:
            parsed = json.loads(content[0]["text"])
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"Failed to parse MCP response for tool={tool_name}: {e}")
            return {"error": f"Failed to parse MCP response: {e}"}
        logger.debug(f"MCP PARSED result keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
        return parsed
    logger.warning(f"MCP returned empty content for tool={tool_name}")
    return {}


def execute_tool(name: str, arguments: dict) -> dict:
    """Route a tool call to the appropriate MCP server."""
    logger.info(f"EXECUTE_TOOL: name={name} arguments={arguments}")
    if name == "search_memories":
        if not MCP_MEMORY_ENDPOINT:
            logger.error("search_memories called but MCP_MEMORY_ENDPOINT not set")
            return {"error": "Memory MCP server not configured"}
        result = _call_mcp_tool_sync(name, arguments, MCP_MEMORY_ENDPOINT)
        logger.info(f"EXECUTE_TOOL search_memories result keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
        return result
    elif name == "search_knowledge":
        if not MCP_SEARCH_ENDPOINT:
            logger.error("search_knowledge called but MCP_SEARCH_ENDPOINT not set")
            return {"error": "Search MCP server not configured"}
        logger.info(f"EXECUTE_TOOL routing search_knowledge to {MCP_SEARCH_ENDPOINT}")
        result = _call_mcp_tool_sync(name, arguments, MCP_SEARCH_ENDPOINT)
        logger.info(f"EXECUTE_TOOL search_knowledge result keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
        return result
    logger.warning(f"EXECUTE_TOOL unknown tool: {name}")
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Agent loop (synchronous — called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _load_user_memories(user_id: str) -> str | None:
    """Fetch user profile from MCP memory server (synchronous). Returns formatted context or None."""
    if not user_id or not MCP_MEMORY_ENDPOINT:
        return None

    result = _call_mcp_tool_sync("get_user_profile", {"user_id": user_id}, MCP_MEMORY_ENDPOINT)
    profile_items = result.get("profile", [])
    if not profile_items:
        return None

    lines = ["User Profile (from previous conversations):"]
    for item in profile_items:
        lines.append(f"- [{item.get('category', '')}] {item.get('content', '')}")
    return "\n".join(lines)


def run_agent(user_id: str | None, message: str, thread_id: str | None = None) -> tuple[str, str]:
    """Run the AI agent with function-calling loop.

    Uses persistent Foundry threads for within-session memory.
    On the first call (no thread_id), creates a new thread with the
    user profile context. On subsequent calls, appends the new user
    message to the existing thread.

    Returns (response_text, thread_id).
    """
    if not openai_project_client or not agent:
        raise RuntimeError("Agent not initialized")

    is_new_thread = thread_id is None

    if is_new_thread:
        # First turn — create a new thread with profile context
        items = []
        if user_id:
            profile_context = _load_user_memories(user_id)
            if profile_context:
                items.append({
                    "type": "message",
                    "role": "developer",
                    "content": profile_context,
                })
            items.append({
                "type": "message",
                "role": "developer",
                "content": f"The current user's ID is: {user_id}. Use this when calling memory tools.",
            })
        items.append({"type": "message", "role": "user", "content": message})

        logger.info(f"AGENT: Creating new thread with {len(items)} items")
        conversation = openai_project_client.conversations.create(items=items)
        thread_id = conversation.id
        logger.info(f"AGENT: Thread created: {thread_id}")
    else:
        # Subsequent turn — append user message to existing thread
        logger.info(f"AGENT: Appending message to existing thread {thread_id}")
        openai_project_client.conversations.items.create(
            conversation_id=thread_id,
            items=[{"type": "message", "role": "user", "content": message}],
        )

    response = None
    for iteration in range(10):  # max tool-calling iterations
        logger.info(f"AGENT: Iteration {iteration + 1} — calling responses.create")
        response = openai_project_client.responses.create(
            conversation=thread_id,
            extra_body={
                "agent_reference": {"name": agent.name, "type": "agent_reference"},
            },
        )

        # Log all output items
        for o in response.output:
            logger.debug(f"AGENT: Output item type={o.type}")

        # Check for function calls
        function_calls = [o for o in response.output if o.type == "function_call"]
        if not function_calls:
            logger.info(f"AGENT: No function calls — final text response (iteration {iteration + 1})")
            break  # final text response

        logger.info(f"AGENT: {len(function_calls)} function call(s): {[fc.name for fc in function_calls]}")

        # Execute each function call and feed results back
        output_items = []
        for fc in function_calls:
            args = json.loads(fc.arguments)
            logger.info(f"AGENT: Calling tool={fc.name} call_id={fc.call_id} args={args}")
            result = execute_tool(fc.name, args)
            result_json = json.dumps(result)
            logger.info(f"AGENT: Tool result for {fc.name}: {result_json[:300]}")
            output_items.append({
                "type": "function_call_output",
                "call_id": fc.call_id,
                "output": result_json,
            })

        openai_project_client.conversations.items.create(
            conversation_id=thread_id,
            items=output_items,
        )
        logger.info(f"AGENT: Fed {len(output_items)} tool result(s) back to conversation")

    final_text = response.output_text if response else ""
    logger.info(f"AGENT: Final response length={len(final_text)} chars")
    return final_text, thread_id


# ---------------------------------------------------------------------------
# Async MCP helper (used by background memory storage)
# ---------------------------------------------------------------------------

async def _call_mcp_tool(tool_name: str, arguments: dict, endpoint: str | None = None) -> dict:
    """Async helper to call any MCP tool via JSON-RPC."""
    mcp_endpoint = endpoint or MCP_MEMORY_ENDPOINT
    if not mcp_endpoint:
        return {"error": "MCP server not configured"}

    mcp_url = f"{mcp_endpoint.rstrip('/')}/mcp"
    jsonrpc_request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": 1,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(mcp_url, json=jsonrpc_request)

    if response.status_code != 200:
        return {"error": f"MCP server returned {response.status_code}"}

    data = response.json()
    if "error" in data:
        return {"error": data["error"].get("message", "Unknown MCP error")}

    content = data.get("result", {}).get("content", [])
    if content and "text" in content[0]:
        try:
            return json.loads(content[0]["text"])
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"Failed to parse async MCP response for tool={tool_name}: {e}")
            return {"error": f"Failed to parse MCP response: {e}"}
    return {}


async def extract_and_save_memories(user_id: str, conversation_id: str, messages: list[dict]):
    """Background task: delegate memory storage to the MCP memory server."""
    logger.info(f"STORE: Starting memory storage for user={user_id} conversation={conversation_id} messages={len(messages)}")
    if not MCP_MEMORY_ENDPOINT:
        logger.warning("STORE: MCP_MEMORY_ENDPOINT not set — skipping memory storage")
        return

    try:
        result = await _call_mcp_tool("store_memories", {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "messages": messages,
        })
        logger.info(f"STORE: Memory storage complete for user={user_id}: {result}")
    except Exception as e:
        logger.error(f"STORE: Memory storage failed for user={user_id}: {e}")


@app.post("/api/chat/stream")
async def chat_stream(request: ChatStreamRequest, background_tasks: BackgroundTasks):
    """
    Streaming chat endpoint powered by the AI agent.
    Uses persistent Foundry threads — the frontend sends thread_id
    after the first turn so the server maintains conversation history.
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    user_message = request.message
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    async def generate():
        try:
            text, thread_id = await asyncio.to_thread(
                run_agent, request.user_id, user_message, request.thread_id
            )
            yield f"data: {json.dumps({'type': 'content', 'text': text})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'thread_id': thread_id})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/chat")
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    """
    Non-streaming chat endpoint powered by the AI agent.
    Uses persistent Foundry threads for conversation continuity.
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    if not request.message:
        raise HTTPException(status_code=400, detail="Message is required")

    try:
        text, thread_id = await asyncio.to_thread(
            run_agent, request.user_id, request.message, request.thread_id
        )
        return {"response": text, "thread_id": thread_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memories/store")
async def store_memories_endpoint(request: StoreMemoriesRequest, background_tasks: BackgroundTasks):
    """
    Store memories from a completed conversation.
    Called by the frontend when a conversation ends (tab close, new chat, etc.).
    Extracts memorable facts via LLM, then saves them to Cosmos DB.
    Runs in the background so the response is immediate.
    """
    logger.info(
        f"STORE: Received memory store request for user={request.user_id} "
        f"conversation={request.conversation_id} messages={len(request.messages)} wait={request.wait}"
    )
    if not request.user_id or not request.messages:
        raise HTTPException(status_code=400, detail="user_id and messages are required")

    msgs = [{"role": m.role, "content": m.content} for m in request.messages]
    if request.wait:
        await extract_and_save_memories(request.user_id, request.conversation_id, msgs)
        logger.info(f"STORE: Completed synchronous memory storage for user={request.user_id}")
        return {"status": "completed", "conversation_id": request.conversation_id}
    background_tasks.add_task(extract_and_save_memories, request.user_id, request.conversation_id, msgs)
    logger.info(f"STORE: Queued background memory storage for user={request.user_id}")
    return {"status": "accepted", "conversation_id": request.conversation_id}


@app.get("/health")
async def health_check():
    """Liveness probe — confirms the process is running."""
    return {"status": "healthy", "agent": agent.name if agent else None}


@app.get("/ready")
async def readiness_check():
    """Readiness probe — returns 503 until the agent is fully initialized."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not ready")
    return {"status": "ready", "agent": agent.name}


@app.get("/api/config")
async def get_config():
    """Get client configuration (non-sensitive)"""
    return {
        "deployment": FOUNDRY_CHAT_MODEL_DEPLOYMENT,
        "agent_configured": agent is not None,
        "mcp_memory_configured": bool(MCP_MEMORY_ENDPOINT),
        "mcp_search_configured": bool(MCP_SEARCH_ENDPOINT),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
