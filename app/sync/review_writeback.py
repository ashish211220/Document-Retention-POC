"""
app/sync/review_writeback.py

Detects retention list rows where DocumentTagged = "Reviewed" (set by Himank's
Power App) and applies the reviewer's decision back to the source file,
Purview label, retention list, and audit log — reusing the existing four-step
sync pipeline. The AI classification is never called.

After all four steps succeed the row's DocumentTagged is set to "Manually Tagged".
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    REVIEW_HUMAN_CONFIDENCE,
    REVIEW_LIST_DOC_TYPE_COL,
    REVIEW_LIST_RETENTION_RULE_COL,
    REVIEW_LIST_TAGGED_COL,
    REVIEW_LIST_TAGGED_DETECT,
    REVIEW_LIST_TAGGED_DONE,
    REVIEW_SETTLE_SECONDS,
    SHAREPOINT_AUDITLOG_LIST_ID,
    SHAREPOINT_RETENTION_LIST_ID,
    SHAREPOINT_SITE_ID,
    SHAREPOINT_SOURCE_DRIVE_ID,
)
from app.models.db_models import ClassificationRecord, DocumentRecord
from app.services.metadata_service import get_taxonomy
from app.services.purview_label_resolver import normalize_retention_rule
from app.services.retention_schedule_service import calculate_retention_schedule
from app.sync.sharepoint_sync import SyncPayload, sync_classification_to_sharepoint
from app.azure.search import _parse_retention_rule

logger = logging.getLogger(__name__)
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_review_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Graph helpers (scoped to this module — no duplication with sharepoint_sync)
# ---------------------------------------------------------------------------

async def _graph_get_all(token: str, url: str) -> List[Dict[str, Any]]:
    """Pages through a Graph list endpoint and returns all items."""
    headers = {"Authorization": f"Bearer {token}"}
    items: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
    return items


async def _graph_patch(token: str, url: str, body: Dict[str, Any]) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(url, headers=headers, json=body)
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Retention list polling
# ---------------------------------------------------------------------------

async def _fetch_reviewed_items(token: str) -> List[Dict[str, Any]]:
    """Return retention list rows where DocumentTagged = REVIEW_LIST_TAGGED_DETECT."""
    url = (
        f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_RETENTION_LIST_ID}/items"
        f"?$expand=fields&$select=id,fields,lastModifiedDateTime,lastModifiedBy"
        f"&$top=100"
    )
    all_items = await _graph_get_all(token, url)
    return [
        item for item in all_items
        if item.get("fields", {}).get(REVIEW_LIST_TAGGED_COL) == REVIEW_LIST_TAGGED_DETECT
    ]


# ---------------------------------------------------------------------------
# DB lookup helpers
# ---------------------------------------------------------------------------

async def _find_cls_record_by_doc_number(
    db: AsyncSession, document_number: int
) -> Optional[ClassificationRecord]:
    stmt = (
        select(ClassificationRecord)
        .join(DocumentRecord, ClassificationRecord.document_id == DocumentRecord.id)
        .where(DocumentRecord.document_number == document_number)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Taxonomy helpers
# ---------------------------------------------------------------------------

def _find_taxonomy_record(document_type: str):
    """Exact match on document_type (case-insensitive)."""
    taxonomy = get_taxonomy()
    norm = document_type.strip().lower()
    for rec in taxonomy.records:
        if rec.document_type.strip().lower() == norm:
            return rec
    return None


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------

async def _apply_one_reviewed_item(
    token: str,
    db: AsyncSession,
    list_item: Dict[str, Any],
) -> str:
    """
    Process a single reviewed retention list row.
    Returns a short outcome string for logging.
    """
    fields = list_item.get("fields", {})
    list_item_id = list_item.get("id", "")
    last_modified_str: str = list_item.get("lastModifiedDateTime", "")
    last_modifier: str = (
        list_item.get("lastModifiedBy", {}).get("user", {}).get("displayName", "unknown")
    )

    # --- Settle delay ---
    if last_modified_str:
        try:
            modified_dt = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - modified_dt).total_seconds() < REVIEW_SETTLE_SECONDS:
                logger.info(
                    f"[ReviewWriteback] List item {list_item_id} modified {last_modified_str} — "
                    f"within settle window ({REVIEW_SETTLE_SECONDS}s), skipping."
                )
                return "settle_pending"
        except ValueError:
            pass

    # --- Parse DocumentID → document_number ---
    raw_doc_id = fields.get("DocumentID", "")
    try:
        document_number = int(str(raw_doc_id).strip())
    except (ValueError, TypeError):
        logger.warning(f"[ReviewWriteback] List item {list_item_id} — missing or invalid DocumentID '{raw_doc_id}'. Skipping.")
        return "unknown_document"

    # --- Reviewer's choices ---
    reviewer_doc_type = (fields.get(REVIEW_LIST_DOC_TYPE_COL) or "").strip()
    reviewer_rule = (fields.get(REVIEW_LIST_RETENTION_RULE_COL) or "").strip()

    # --- Plain confirmation: Reviewed with no reviewer values → accept AI proposal ---
    use_ai_proposal = not reviewer_doc_type and not reviewer_rule

    # --- Find DB record ---
    cls_record = await _find_cls_record_by_doc_number(db, document_number)
    if cls_record is None:
        logger.warning(f"[ReviewWriteback] No DB record for DocumentID={document_number}. Skipping.")
        return "unknown_document"

    # --- Idempotency: skip if version already applied ---
    if cls_record.review_applied_version and cls_record.review_applied_version == last_modified_str:
        logger.info(f"[ReviewWriteback] DocumentID={document_number} — version already applied, skipping.")
        return "already_applied"

    # --- Resolve document type and taxonomy record ---
    if use_ai_proposal:
        # Accept stored AI proposal
        effective_doc_type = cls_record.document_type or ""
        effective_rule = cls_record.retention_rule or ""
        logger.info(f"[ReviewWriteback] DocumentID={document_number} — plain confirmation, using AI proposal.")
    else:
        effective_doc_type = reviewer_doc_type
        effective_rule = reviewer_rule

    tax_record = _find_taxonomy_record(effective_doc_type)
    if tax_record is None:
        logger.warning(f"[ReviewWriteback] DocumentID={document_number} — unknown document_type '{effective_doc_type}'. Skipping.")
        return "unknown_document_type"

    # --- Resolve Purview label using existing mapping (rule → label name) ---
    normalized_rule = normalize_retention_rule(effective_rule)
    from app.services.purview_label_resolver import _PER_RULE_MAP
    if normalized_rule not in _PER_RULE_MAP:
        logger.warning(f"[ReviewWriteback] DocumentID={document_number} — no label mapping for rule '{effective_rule}'. Skipping.")
        return "no_label_mapping"

    # --- Log if reviewer label differs from taxonomy ---
    taxonomy_rule = tax_record.retention_rule or ""
    if effective_rule and taxonomy_rule and normalize_retention_rule(effective_rule) != normalize_retention_rule(taxonomy_rule):
        logger.warning(
            f"[ReviewWriteback] DocumentID={document_number} — reviewer rule '{effective_rule}' "
            f"differs from taxonomy rule '{taxonomy_rule}' for '{effective_doc_type}'. Reviewer wins."
        )
        audit_note = (
            f"Reviewer rule '{effective_rule}' used (taxonomy default: '{taxonomy_rule}')."
        )
    else:
        audit_note = None

    # --- Compute retention schedule ---
    code, period, unit = _parse_retention_rule(effective_rule)
    schedule = calculate_retention_schedule(
        retention_rule=effective_rule,
        document_date=None,  # reference date not available without AI run
    )
    if not schedule.retention_end_date:
        logger.info(f"[ReviewWriteback] DocumentID={document_number} — missing reference date, end date left empty.")

    confidence = REVIEW_HUMAN_CONFIDENCE / 100.0  # stored as 0.0–1.0

    # --- Preserve original AI values before first human review ---
    if cls_record.classified_by != "human":
        if cls_record.ai_proposed_category is None:
            cls_record.ai_proposed_category = cls_record.category
        if cls_record.ai_confidence_score is None:
            cls_record.ai_confidence_score = cls_record.confidence_score

    # --- Build SyncPayload (reuses the existing four-step sync) ---
    payload = SyncPayload(
        classification_record_id=cls_record.id,
        document_name=cls_record.document.name if cls_record.document else str(document_number),
        document_type=effective_doc_type,
        category=tax_record.category,
        section=tax_record.section,
        retention_code=code or tax_record.retention_code,
        retention_rule=effective_rule,
        retention_end_date=schedule.retention_end_date or "",
        confidence_score=confidence,
        team_owner=tax_record.team_owner,
        classification_status="Reviewed",
        document_number=document_number,
        drive_id=SHAREPOINT_SOURCE_DRIVE_ID,
        sharepoint_item_id=cls_record.sharepoint_item_id,
        sharepoint_list_item_id=list_item_id,
        sharepoint_web_url=cls_record.sharepoint_web_url or "",
        purview_label_applied=False,   # always re-apply on human review
        triggered_by=last_modifier,
        audit_action="human_review_applied",
        audit_notes=audit_note,
        force_purview_label=True,      # bypass AI confidence gate
    )

    sync_result = await sync_classification_to_sharepoint(token, payload, db)

    if not sync_result.fully_successful:
        logger.warning(
            f"[ReviewWriteback] DocumentID={document_number} — partial sync failure: {sync_result.errors}"
        )
        return "partial_failure"

    # --- Mark DocumentTagged = "Manually Tagged" (only after full success) ---
    patch_url = (
        f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_RETENTION_LIST_ID}"
        f"/items/{list_item_id}/fields"
    )
    await _graph_patch(token, patch_url, {REVIEW_LIST_TAGGED_COL: REVIEW_LIST_TAGGED_DONE})

    # --- Update PostgreSQL record ---
    now = datetime.now(timezone.utc)
    cls_record.classified_by = "human"
    cls_record.reviewer = last_modifier
    cls_record.reviewed_at = now
    cls_record.review_applied_version = last_modified_str
    cls_record.review_applied_at = now
    cls_record.document_type = effective_doc_type
    cls_record.category = tax_record.category
    cls_record.team_owner = tax_record.team_owner
    cls_record.retention_rule = effective_rule
    cls_record.retention_code = code or tax_record.retention_code
    cls_record.retention_period = str(period) if period else None
    cls_record.retention_period_unit = unit
    cls_record.retention_end_date = schedule.retention_end_date or ""
    cls_record.confidence_score = confidence
    cls_record.status = "Reviewed"
    cls_record.purview_label_applied = True
    cls_record.purview_label_applied_at = now
    await db.commit()

    logger.info(f"[ReviewWriteback] DocumentID={document_number} — write-back complete. Reviewer: {last_modifier}.")
    return "applied"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_review_writeback(token: str, db: AsyncSession) -> Dict[str, Any]:
    """
    Scans the retention list for Reviewed rows and applies human decisions.
    Called at the start of each scheduler poll cycle and once at startup.
    Protected by _review_lock so overlapping cycles never double-process.
    """
    if _review_lock.locked():
        logger.info("[ReviewWriteback] Previous cycle still running — skipping.")
        return {"skipped": True}

    async with _review_lock:
        if not all([SHAREPOINT_SITE_ID, SHAREPOINT_RETENTION_LIST_ID]):
            logger.warning("[ReviewWriteback] Missing SHAREPOINT_SITE_ID or SHAREPOINT_RETENTION_LIST_ID — skipping.")
            return {"skipped": True}

        summary: Dict[str, int] = {
            "applied": 0, "already_applied": 0, "settle_pending": 0,
            "partial_failure": 0, "error": 0,
        }

        try:
            items = await _fetch_reviewed_items(token)
        except Exception as exc:
            logger.error(f"[ReviewWriteback] Failed to fetch reviewed items: {exc}")
            return {"error": str(exc)}

        if not items:
            logger.info("[ReviewWriteback] No Reviewed items found.")
            return summary

        # Deduplicate: if multiple rows share a DocumentID, use the most recently modified
        seen: Dict[str, Dict] = {}
        for item in items:
            raw_id = str(item.get("fields", {}).get("DocumentID", "")).strip()
            existing = seen.get(raw_id)
            if existing is None or item.get("lastModifiedDateTime", "") > existing.get("lastModifiedDateTime", ""):
                if existing is not None:
                    logger.warning(f"[ReviewWriteback] Duplicate retention rows for DocumentID={raw_id}. Using most recent.")
                seen[raw_id] = item

        for item in seen.values():
            try:
                outcome = await _apply_one_reviewed_item(token, db, item)
                summary[outcome] = summary.get(outcome, 0) + 1
            except Exception as exc:
                doc_id = item.get("fields", {}).get("DocumentID", "?")
                logger.error(f"[ReviewWriteback] Unhandled error for DocumentID={doc_id}: {exc}")
                summary["error"] += 1

        logger.info(f"[ReviewWriteback] Cycle complete — {summary}")
        return summary
