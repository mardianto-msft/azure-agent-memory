#!/usr/bin/env python3
"""
MCP Search Server — hybrid search over Azure AI Search knowledge base.
Exposes a single `search_knowledge` tool via JSON-RPC.
"""

import json
import logging
import os

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="MCP Search Server")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mcp-search")

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
AZURE_AI_SEARCH_ENDPOINT = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
INDEX_NAME = os.getenv("INDEX_NAME", "knowledge-index")

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
credential = DefaultAzureCredential()

search_client = (
    SearchClient(
        endpoint=AZURE_AI_SEARCH_ENDPOINT,
        index_name=INDEX_NAME,
        credential=credential,
    )
    if AZURE_AI_SEARCH_ENDPOINT
    else None
)

# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the knowledge base using hybrid search (text + vector + semantic reranking). "
            "Returns the most relevant chunks from the indexed documentation."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query in natural language.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5).",
                    "default": 5,
                },
            },
        },
    }
]


async def handle_search_knowledge(arguments: dict) -> dict:
    """Execute hybrid search against the Azure AI Search index."""
    if not search_client:
        return {"error": "Azure AI Search not configured"}

    query = arguments.get("query", "")
    top_k = arguments.get("top_k", 5)

    vector_query = VectorizableTextQuery(
        text=query,
        k_nearest_neighbors=top_k,
        fields="embedding",
    )

    results = search_client.search(
        search_text=query,
        vector_queries=[vector_query],
        query_type="semantic",
        semantic_configuration_name="semantic_config",
        top=top_k,
        select=["id", "content", "source_url", "chunk_index"],
    )

    chunks = []
    for result in results:
        chunks.append({
            "id": result["id"],
            "content": result["content"],
            "source_url": result.get("source_url", ""),
            "chunk_index": result.get("chunk_index", 0),
            "score": result.get("@search.score", 0),
            "reranker_score": result.get("@search.reranker_score"),
        })

    return {"results": chunks, "count": len(chunks)}


TOOL_HANDLERS = {
    "search_knowledge": handle_search_knowledge,
}


# ---------------------------------------------------------------------------
# JSON-RPC endpoint (MCP protocol)
# ---------------------------------------------------------------------------

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """Handle MCP JSON-RPC requests."""
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    # initialize
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcp-search", "version": "1.0.0"},
            },
        })

    # notifications — acknowledge silently
    if method and method.startswith("notifications/"):
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    # tools/list
    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        })

    # tools/call
    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            })

        result = await handler(arguments)
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result)}],
            },
        })

    # Unknown method
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/")
async def root():
    return {"service": "mcp-search", "status": "running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
