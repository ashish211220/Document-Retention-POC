import json
import os
from typing import List, Optional
from app.models.retention import RetentionRecord, RetentionTaxonomy
from app.config import AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY
from app.utils.logger import get_logger

logger = get_logger(__name__)

_taxonomy: Optional[RetentionTaxonomy] = None

TAXONOMY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "retention_taxonomy.json",
)

_azure_search_enabled = bool(AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY)


def load_taxonomy() -> RetentionTaxonomy:
    global _taxonomy
    if _taxonomy is not None:
        return _taxonomy

    logger.info(f"Loading retention taxonomy from: {TAXONOMY_PATH}")
    if not os.path.exists(TAXONOMY_PATH):
        raise FileNotFoundError(
            f"Retention taxonomy not found at: {TAXONOMY_PATH}. "
            "Ensure data/retention_taxonomy.json exists."
        )

    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    from app.azure.search import _parse_retention_rule

    records = []
    for item in raw:
        if "retention_code" not in item:
            code, period, unit = _parse_retention_rule(item.get("retention_rule", ""))
            item["retention_code"] = code
            item["retention_period"] = period
            item["retention_period_unit"] = unit
        records.append(RetentionRecord(**item))

    _taxonomy = RetentionTaxonomy.from_list(records)
    logger.info(f"Retention taxonomy loaded: {_taxonomy.total_count} records.")

    if _azure_search_enabled:
        logger.info(
            "Azure AI Search is configured — semantic/keyword search will be used."
        )
    else:
        logger.warning(
            "Azure AI Search is NOT configured — falling back to local keyword search."
        )

    return _taxonomy


def get_taxonomy() -> RetentionTaxonomy:
    if _taxonomy is None:
        return load_taxonomy()
    return _taxonomy


def search_by_keywords(keywords: List[str], top_k: int = 5) -> List[RetentionRecord]:
    if _azure_search_enabled:
        return _azure_search(keywords, top_k)
    return _local_search(keywords, top_k)


def _azure_search(keywords: List[str], top_k: int) -> List[RetentionRecord]:
    from app.config import SEARCH_MODE

    query = " ".join(keywords)
    logger.info(f"Azure AI Search query: '{query}' (top {top_k}) [Mode: {SEARCH_MODE}]")

    if SEARCH_MODE == "hybrid":
        from app.azure.search import hybrid_search

        raw_results = hybrid_search(query, top_k=top_k)
    else:
        from app.azure.search import keyword_search

        raw_results = keyword_search(query, top_k=top_k)

    records = []
    for r in raw_results:
        kw_str = r.get("keywords_string", "")
        try:
            record = RetentionRecord(
                id=r["id"].replace("-", "."),
                category=r.get("category", ""),
                section=r.get("section", ""),
                document_type=r.get("document_type", ""),
                retention_label=r.get("retention_label", ""),
                retention_rule=r.get("retention_rule", ""),
                retention_code=r.get("retention_code"),
                retention_period=r.get("retention_period"),
                retention_period_unit=r.get("retention_period_unit"),
                classification=r.get("classification", ""),
                team_owner=r.get("team_owner", ""),
                expected_location="",
                description=r.get("description", ""),
                keywords=kw_str.split() if kw_str else [],
                policy_version=r.get("policy_version", "1.0"),
            )
            records.append(record)
        except Exception as e:
            logger.warning(f"Skipping malformed search result: {e}")
    return records


def _local_search(keywords: List[str], top_k: int) -> List[RetentionRecord]:
    taxonomy = get_taxonomy()
    kw_lower = [k.lower() for k in keywords]
    scored: List[tuple] = []
    for record in taxonomy.records:
        record_text = (
            f"{record.document_type} {record.category} {record.section} "
            f"{record.description} {' '.join(record.keywords)}"
        ).lower()
        score = sum(1 for kw in kw_lower if kw in record_text)
        if score > 0:
            scored.append((score, record))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def get_record_by_id(record_id: str) -> Optional[RetentionRecord]:
    taxonomy = get_taxonomy()
    for record in taxonomy.records:
        if record.id == record_id:
            return record
    return None
