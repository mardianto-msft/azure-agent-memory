#!/usr/bin/env python3
"""
Knowledge Base Ingestion Script
Crawls specified URLs, chunks the content, generates embeddings via Azure OpenAI,
creates an Azure AI Search index, and uploads all chunks.
Run post-deployment: python scripts/ingest_knowledge.py
"""

import os
from pathlib import Path

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient, IndexDocumentsBatch
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    SearchField,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchProfile,
)
from langchain_community.document_loaders import WebBaseLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import AzureOpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FOUNDRY_ENDPOINT = os.environ["FOUNDRY_ENDPOINT"]
EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_AI_FOUNDRY_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
EMBEDDING_MODEL = "text-embedding-3-large"
SEARCH_ENDPOINT = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
INDEX_NAME = "knowledge-index"
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "3072"))

# ---------------------------------------------------------------------------
# URLs to ingest — edit scripts/knowledge_urls.txt to add or remove URLs
# ---------------------------------------------------------------------------
URLS_FILE = Path(__file__).parent / "knowledge_urls.txt"


def load_urls(path: Path = URLS_FILE) -> list[str]:
    """Load URLs from a text file. Skips blank lines and comments (#)."""
    lines = path.read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def check_index_has_documents(search_endpoint: str, index_name: str) -> bool:
    """Check if the search index already has documents."""
    try:
        credential = DefaultAzureCredential()
        client = SearchClient(endpoint=search_endpoint, index_name=index_name, credential=credential)
        results = client.search(search_text="*", top=1, include_total_count=True)
        total_count = results.get_count()
        return total_count is not None and total_count >= 1
    except Exception as e:
        print(f"  Index check (may not exist yet): {e}")
        return False


def create_search_index(index_client: SearchIndexClient):
    """Create the Azure AI Search index with vector search and semantic configuration."""
    index = SearchIndex(
        name=INDEX_NAME,
        fields=[
            SearchField(name="id", type="Edm.String", key=True, filterable=True, sortable=True),
            SearchField(name="content", type="Edm.String", filterable=False, sortable=False),
            SearchField(name="source_url", type="Edm.String", filterable=True, sortable=True),
            SearchField(name="chunk_index", type="Edm.Int32", filterable=True, sortable=True),
            SearchField(
                name="embedding",
                type="Collection(Edm.Single)",
                stored=False,
                vector_search_dimensions=EMBEDDING_DIMENSIONS,
                vector_search_profile_name="hnsw_profile",
            ),
        ],
        vector_search=VectorSearch(
            profiles=[
                VectorSearchProfile(
                    name="hnsw_profile",
                    algorithm_configuration_name="alg",
                    vectorizer_name="azure_openai_vectorizer",
                ),
            ],
            algorithms=[HnswAlgorithmConfiguration(name="alg")],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="azure_openai_vectorizer",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=FOUNDRY_ENDPOINT,
                        deployment_name=EMBEDDING_DEPLOYMENT,
                        model_name=EMBEDDING_MODEL,
                    ),
                ),
            ],
        ),
        semantic_search=SemanticSearch(
            default_configuration_name="semantic_config",
            configurations=[
                SemanticConfiguration(
                    name="semantic_config",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[SemanticField(field_name="content")]
                    ),
                ),
            ],
        ),
    )

    index_client.create_or_update_index(index)
    print(f"Index '{INDEX_NAME}' created or updated.")


def embed(openai_client: AzureOpenAI, text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    resp = openai_client.embeddings.create(input=text, model=EMBEDDING_DEPLOYMENT)
    return resp.data[0].embedding


def ingest():
    """Main ingestion pipeline: crawl → chunk → embed → upload."""
    urls = load_urls()
    if not urls:
        print(f"No URLs configured. Add URLs to {URLS_FILE}")
        return

    # Skip if index already populated (set FORCE_REPROCESS=true to override)
    force = os.getenv("FORCE_REPROCESS", "false").lower() == "true"
    if not force and check_index_has_documents(SEARCH_ENDPOINT, INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already contains documents. Skipping.")
        print("  To force reprocessing, run: azd env set FORCE_REPROCESS true")
        return

    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )

    openai_client = AzureOpenAI(
        azure_endpoint=FOUNDRY_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version="2024-10-21",
    )
    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=credential)

    # 1. Crawl
    print(f"Loading {len(urls)} URL(s)...")
    loader = WebBaseLoader(web_paths=urls)
    docs = loader.load()
    print(f"Loaded {len(docs)} document(s).")

    # 2. Chunk
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.split_documents(docs)
    print(f"Split into {len(chunks)} chunk(s).")

    # 3. Create index
    create_search_index(index_client)

    # 4. Embed and upload
    documents = []
    for i, chunk in enumerate(chunks):
        source_url = chunk.metadata.get("source", "unknown")
        if i % 10 == 0:
            print(f"  Generating embeddings... {i}/{len(chunks)}")
        vector = embed(openai_client, chunk.page_content)

        documents.append({
            "id": f"chunk-{i}",
            "content": chunk.page_content,
            "source_url": source_url,
            "chunk_index": i,
            "embedding": vector,
        })

    print(f"Uploading {len(documents)} documents to index...")
    search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=credential)
    batch = IndexDocumentsBatch()
    batch.add_upload_actions(documents)
    results = search_client.index_documents(batch)

    succeeded = sum(1 for r in results if r.succeeded)
    failed = sum(1 for r in results if not r.succeeded)
    if failed:
        for r in results:
            if not r.succeeded:
                print(f"  FAILED: {r.key} — {r.error_message}")
        print(f"ERROR: {failed}/{len(documents)} documents failed to upload.")
    else:
        print(f"Done! Uploaded {succeeded} chunks to index '{INDEX_NAME}'.")


if __name__ == "__main__":
    ingest()
