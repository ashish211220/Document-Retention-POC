"""
Azure AI Search client and operations for the Retention Taxonomy index.

This module handles:
- SearchClient initialization
- Index creation (schema definition)
- Document upload / upsert
- Keyword search
- Hybrid search (keyword + semantic) — Phase 4 ready
"""
from typing import List, Optional
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    CorsOptions,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from app.azure.openai import generate_embedding
from app.config import AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY, AZURE_SEARCH_INDEX_NAME
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Clients ──────────────────────────────────────────────────────────────────

def _get_index_client() -> SearchIndexClient:
    return SearchIndexClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        credential=AzureKeyCredential(AZURE_SEARCH_API_KEY)
    )


def _get_search_client() -> SearchClient:
    return SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX_NAME,
        credential=AzureKeyCredential(AZURE_SEARCH_API_KEY)
    )


# ── Index Schema ──────────────────────────────────────────────────────────────

INDEX_FIELDS = [
    SimpleField(name="id",                 type=SearchFieldDataType.String, key=True,       filterable=True),
    SearchableField(name="category",       type=SearchFieldDataType.String, filterable=True, analyzer_name="en.microsoft"),
    SearchableField(name="section",        type=SearchFieldDataType.String, filterable=True, analyzer_name="en.microsoft"),
    SearchableField(name="document_type",  type=SearchFieldDataType.String,                  analyzer_name="en.microsoft"),
    SearchableField(name="retention_label",type=SearchFieldDataType.String,                  analyzer_name="en.microsoft"),
    SimpleField(name="retention_rule",     type=SearchFieldDataType.String, filterable=True),
    SimpleField(name="retention_code",     type=SearchFieldDataType.String, filterable=True),
    SimpleField(name="retention_period",   type=SearchFieldDataType.Int32),
    SimpleField(name="retention_period_unit", type=SearchFieldDataType.String),
    SimpleField(name="classification",     type=SearchFieldDataType.String, filterable=True),
    SearchableField(name="team_owner",     type=SearchFieldDataType.String, filterable=True, analyzer_name="en.microsoft"),
    SearchableField(name="description",    type=SearchFieldDataType.String,                  analyzer_name="en.microsoft"),
    SearchableField(name="keywords_string",type=SearchFieldDataType.String,                  analyzer_name="en.microsoft"),
    SimpleField(name="policy_version",     type=SearchFieldDataType.String),
    SearchField(name="embedding", type=SearchFieldDataType.Collection(SearchFieldDataType.Single), searchable=True, vector_search_dimensions=1536, vector_search_profile_name="myHnswProfile"),
]


def create_index_if_not_exists() -> bool:
    """
    Create the retention taxonomy index in Azure AI Search.
    Returns True if index was created, False if it already existed.
    """
    client = _get_index_client()
    existing = [idx if isinstance(idx, str) else idx.name for idx in client.list_index_names()]
    if AZURE_SEARCH_INDEX_NAME in existing:
        logger.info(f"Index '{AZURE_SEARCH_INDEX_NAME}' already exists — skipping creation.")
        return False

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="myHnsw")],
        profiles=[VectorSearchProfile(name="myHnswProfile", algorithm_configuration_name="myHnsw")]
    )

    index = SearchIndex(
        name=AZURE_SEARCH_INDEX_NAME,
        fields=INDEX_FIELDS,
        cors_options=CorsOptions(allowed_origins=["*"]),
        vector_search=vector_search
    )
    client.create_index(index)
    logger.info(f"Index '{AZURE_SEARCH_INDEX_NAME}' created successfully.")
    return True


def delete_index() -> None:
    """Delete the index (useful for re-indexing)."""
    client = _get_index_client()
    client.delete_index(AZURE_SEARCH_INDEX_NAME)
    logger.info(f"Index '{AZURE_SEARCH_INDEX_NAME}' deleted.")


import re

def _parse_retention_rule(rule: str) -> tuple:
    """
    Parses a combined retention_rule (e.g., 'FE-5', 'AC+10', '3 MONTHS', 'AV') 
    into (retention_code, retention_period, retention_period_unit).
    """
    rule = (rule or "").strip().upper()
    
    # Check for "X MONTHS"
    month_match = re.search(r"(\d+)\s*MONTHS?", rule)
    if month_match:
        return ("TIME", int(month_match.group(1)), "months")
    
    # Check for standard prefixes: FE+5, FE-5, AC, US+10, etc.
    # Matches letters like FE, CE, AC, US, LA optionally followed by + or - and digits.
    match = re.match(r"^([A-Z]+)[-+]?(\d+)?$", rule)
    if match:
        code = match.group(1)
        # If the code isn't one of the mapped ones, maybe leave it as is or map to a default?
        # The prompt says: "Confirm these 6 labels exist in Purview... FE, CE, AC, US, LA, TIME"
        # Since some are UO, CD, YE, we can map YE to CE. 
        if code == "YE":
            code = "CE"
        period = int(match.group(2)) if match.group(2) else None
        unit = "years" if period else None
        return (code, period, unit)
        
    # Default fallback
    return (rule, None, None)

# ── Document Upload ───────────────────────────────────────────────────────────

def upload_taxonomy_documents(records: List[dict]) -> int:
    """
    Upload (merge-or-upload) taxonomy records into the search index.
    Records should already be in the flat dict format (no nested lists for keywords).
    Returns the number of successfully uploaded documents.
    """
    client = _get_search_client()

    # Flatten keywords list → space-joined string for full-text search
    docs = []
    for rec in records:
        doc = dict(rec)
        keywords = rec.get("keywords", [])
        doc["keywords_string"] = " ".join(keywords)
        
        # Azure Search document keys cannot contain periods ('.')
        # Safely convert them to dashes for indexing.
        if "id" in doc:
            doc["id"] = str(doc["id"]).replace(".", "-")
        
        # Extract retention_code and retention_period during ingestion
        rule = doc.get("retention_rule", "")
        code, period, unit = _parse_retention_rule(rule)
        doc["retention_code"] = code
        doc["retention_period"] = period
        doc["retention_period_unit"] = unit
        
        # Generate embedding
        semantic_text = f"Category: {doc.get('category', '')} Section: {doc.get('section', '')} Document Type: {doc.get('document_type', '')} Description: {doc.get('description', '')} Keywords: {doc['keywords_string']}"
        try:
            doc["embedding"] = generate_embedding(semantic_text)
        except Exception as e:
            logger.error(f"Failed to generate embedding for {doc.get('id')}: {e}")

        # Remove fields not in index schema
        doc.pop("keywords", None)
        doc.pop("expected_location", None)
        docs.append(doc)

    # Upload in batches of 100 (Azure Search max batch is 1000, but 100 is safe)
    batch_size = 100
    total_uploaded = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i: i + batch_size]
        result = client.merge_or_upload_documents(documents=batch)
        succeeded = sum(1 for r in result if r.succeeded)
        total_uploaded += succeeded
        logger.info(f"Uploaded batch {i // batch_size + 1}: {succeeded}/{len(batch)} documents.")

    return total_uploaded


# ── Search Functions ──────────────────────────────────────────────────────────

def hybrid_search(query: str, top_k: int = 5, filter_expr: Optional[str] = None) -> List[dict]:
    """
    Hybrid search (keyword + vector) over the retention taxonomy index.

    Args:
        query: Free-text search query.
        top_k: Maximum number of results to return.
        filter_expr: Optional OData filter.

    Returns:
        List of matching taxonomy record dicts.
    """
    client = _get_search_client()
    
    try:
        embedding = generate_embedding(query)
        vector_query = VectorizedQuery(
            vector=embedding, k_nearest_neighbors=top_k, fields="embedding"
        )
        vector_queries = [vector_query]
    except Exception as e:
        logger.warning(f"Failed to generate embedding for search, falling back to pure keyword search: {e}")
        vector_queries = None

    results = client.search(
        search_text=query,
        vector_queries=vector_queries,
        top=top_k,
        filter=filter_expr,
        select=[
            "id", "category", "section", "document_type", "retention_label",
            "retention_rule", "retention_code", "retention_period", "retention_period_unit", 
            "classification", "team_owner", "description",
            "keywords_string", "policy_version"
        ],
        include_total_count=False,
    )
    return [dict(r) for r in results]


def keyword_search(query: str, top_k: int = 5, filter_expr: Optional[str] = None) -> List[dict]:
    """
    Full-text keyword search over the retention taxonomy index.

    Args:
        query: Free-text search query.
        top_k: Maximum number of results to return.
        filter_expr: Optional OData filter (e.g. "classification eq 'Confidential'").

    Returns:
        List of matching taxonomy record dicts with a `@search.score` field.
    """
    client = _get_search_client()
    results = client.search(
        search_text=query,
        top=top_k,
        filter=filter_expr,
        select=[
            "id", "category", "section", "document_type", "retention_label",
            "retention_rule", "retention_code", "retention_period", "retention_period_unit",
            "classification", "team_owner", "description",
            "keywords_string", "policy_version"
        ],
        include_total_count=False,
    )
    return [dict(r) for r in results]


def filter_search(filter_expr: str, top_k: int = 10) -> List[dict]:
    """
    Filter-only search (no text scoring) — useful for looking up by owner or classification.

    Args:
        filter_expr: OData filter expression
            e.g. "team_owner eq 'HR'" or "classification eq 'Confidential'"

    Returns:
        List of matching taxonomy record dicts.
    """
    client = _get_search_client()
    results = client.search(
        search_text="*",
        top=top_k,
        filter=filter_expr,
        select=[
            "id", "category", "section", "document_type", "retention_label",
            "retention_rule", "retention_code", "retention_period", "retention_period_unit",
            "classification", "team_owner", "description",
            "keywords_string", "policy_version"
        ],
    )
    return [dict(r) for r in results]


def get_index_stats() -> dict:
    """Return the number of documents indexed."""
    client = _get_index_client()
    stats = client.get_index_statistics(AZURE_SEARCH_INDEX_NAME)
    return {
        "document_count": stats.document_count,
        "storage_size_bytes": stats.storage_size
    }
