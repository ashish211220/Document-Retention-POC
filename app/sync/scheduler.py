import asyncio
from contextlib import asynccontextmanager
import logging
import uuid
from datetime import datetime, timezone, timedelta, date
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    MAX_PROCESSING_RETRIES,
    SHAREPOINT_SITE_ID,
    SHAREPOINT_SOURCE_DRIVE_ID,
    SYNC_BATCH_CONCURRENCY,
    SYNC_POLL_INTERVAL_MINUTES,
)
from app.db.database import async_session

def _is_postgres_db() -> bool:
    try:
        from app.db.database import is_postgres
        return is_postgres()
    except Exception:
        return False
from app.models.db_models import ClassificationRecord, DocumentRecord, SharePointSyncLog
from app.sync.sharepoint_sync import SyncPayload, sync_classification_to_sharepoint

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_poll_lock = asyncio.Lock()  # Only one poll cycle at a time within this process
_shutdown_event = asyncio.Event()  # Set on app shutdown to cleanly break the loop

# 64-bit integer constant representing the distributed scheduler advisory lock
SCHEDULER_POLL_ADVISORY_LOCK_ID = 74829103


@asynccontextmanager
async def _acquire_distributed_poll_lock():
    """
    Acquires a cross-process distributed lock for the poll cycle.
    Uses PostgreSQL session-level advisory locks if running on Postgres, so multiple
    Uvicorn worker processes or container replicas never poll concurrently.
    Falls back gracefully if running on SQLite or if DB is unconfigured.
    """
    if async_session is None or not _is_postgres_db():
        yield True
        return

    async with async_session() as session:
        got_lock = False
        try:
            from sqlalchemy import text
            res = await session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": SCHEDULER_POLL_ADVISORY_LOCK_ID},
            )
            got_lock = bool(res.scalar())
        except Exception as exc:
            logger.warning(f"[Scheduler] Failed to check PostgreSQL advisory lock: {exc}")
            yield True
            return

        if not got_lock:
            logger.info(
                "[Scheduler] Another worker process currently holds the distributed poll lock. "
                "Skipping this cycle."
            )
            yield False
            return

        try:
            yield True
        finally:
            try:
                from sqlalchemy import text
                await session.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": SCHEDULER_POLL_ADVISORY_LOCK_ID},
                )
            except Exception as unlock_err:
                logger.warning(f"[Scheduler] Error releasing PostgreSQL advisory lock: {unlock_err}")



async def _with_backoff(coro_factory, label: str, max_total_wait: float = 120.0):
    delay = 2.0
    total_waited = 0.0
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (429, 503) and total_waited < max_total_wait:
                wait = min(delay, max_total_wait - total_waited)
                logger.warning(
                    f"[Backoff] {label} got {status}. Waiting {wait:.0f}s "
                    f"(attempt {attempt + 1}, total waited {total_waited:.0f}s)"
                )
                await asyncio.sleep(wait)
                total_waited += wait
                delay = min(delay * 2, 60.0)
                attempt += 1
            else:
                raise
        except (asyncio.TimeoutError, httpx.TimeoutException):
            if total_waited < max_total_wait:
                wait = min(delay, max_total_wait - total_waited)
                logger.warning(
                    f"[Backoff] {label} timed out. Waiting {wait:.0f}s "
                    f"(attempt {attempt + 1})"
                )
                await asyncio.sleep(wait)
                total_waited += wait
                delay = min(delay * 2, 60.0)
                attempt += 1
            else:
                raise


# Retrieves all files from the configured SharePoint drive
async def _list_drive_items(token: str, drive_id: str) -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE}/drives/{drive_id}/root/children?$top=100&$select=id,name,lastModifiedDateTime,webUrl,file&$expand=listItem($select=id)"
    items: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            resp = await _with_backoff(
                lambda u=url: client.get(u, headers=headers),
                label="list_drive_items",
            )
            resp.raise_for_status()
            data = resp.json()
            items.extend(item for item in data.get("value", []) if "file" in item)
            url = data.get("@odata.nextLink")

    return items


def _get_current_token(token: Optional[str] = None) -> str:
    """
    Returns a fresh Graph API token via graph_auth_service (utilizing MSAL in-memory caching),
    falling back to the passed token if graph_auth_service is not configured (e.g. in tests).
    """
    try:
        from app.services.graph_auth_service import graph_auth_service

        if graph_auth_service is not None:
            return graph_auth_service.get_access_token()
    except Exception:
        pass
    return token or ""


# Downloads the actual binary file content from SharePoint
async def _download_drive_item(token: str, drive_id: str, item_id: str) -> bytes:
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


# Checks if this document already exists in the SharePoint Retention list
async def _query_existing_list_item(
    token: str, site_id: str, retention_list_id: str, document_number: int
) -> Optional[str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly",
    }
    url = (
        f"{GRAPH_BASE}/sites/{site_id}/lists/{retention_list_id}/items"
        f"?$filter=fields/DocumentID eq {document_number}&$expand=fields&$select=id,fields"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                items = resp.json().get("value", [])
                if items:
                    return items[0].get("id")
    except Exception as exc:
        logger.warning(f"[Scheduler] Duplicate-check query failed (non-fatal): {exc}")
    return None


def _parse_sp_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _determine_action(
    db: AsyncSession,
    item: Dict[str, Any],
) -> str:
    item_id = item["id"]
    file_modified = _parse_sp_datetime(item.get("lastModifiedDateTime"))

    stmt = select(ClassificationRecord).where(
        ClassificationRecord.sharepoint_item_id == item_id
    )
    result = await db.execute(stmt)
    cls_record: Optional[ClassificationRecord] = result.scalars().first()

    if cls_record is None:
        return "new"

    # 1. Legal hold guard: Documents under legal hold must never be reclassified
    if (
        getattr(cls_record, "legal_hold", False)
        or getattr(cls_record, "review_status", None) == "legal_hold"
    ):
        logger.info(f"[Scheduler] Skipping {item_id} — document is under legal hold.")
        return "skip"

    # 2. Approved deletion guard: Documents approved for deletion or deleted must not be reclassified
    if (
        getattr(cls_record, "review_status", None) == "approved_deletion"
        or getattr(cls_record, "deletion_status", None) in ("Deletion Approved", "Deleted")
    ):
        logger.info(f"[Scheduler] Skipping {item_id} — deletion is approved or executed.")
        return "skip"

    # 3. Human-reviewed documents guard: Human decisions are the source of truth in this system
    # and must never be silently overwritten by subsequent AI poll cycles.
    if (
        getattr(cls_record, "classified_by", None) == "human"
        or getattr(cls_record, "document_tagged_status", None) in ("Reviewed", "Manually Tagged")
    ):
        logger.info(f"[Scheduler] Skipping {item_id} — human review decision already recorded.")
        return "skip"

    status = cls_record.processing_status or "pending"

    if status == "failed_permanent":
        return "skip_permanent"

    if status == "source_deleted":
        return "skip_permanent"

    if status == "processing":
        return "resume"

    # Failed documents must be retried if attempts < MAX_PROCESSING_RETRIES,
    # regardless of modified dates (since DB updated_at was bumped when recording failure).
    if status == "failed":
        attempts = getattr(cls_record, "processing_attempts", 0) or 0
        if attempts >= MAX_PROCESSING_RETRIES:
            return "skip_permanent"
        return "reclassify"

    # Only completed records use modified date checks to skip unchanged files
    # or files whose modified date was updated by our own writeback.
    if status == "completed":
        sp_last_mod = getattr(cls_record, "sharepoint_last_modified", None)
        if file_modified and sp_last_mod and file_modified <= sp_last_mod:
            return "skip"

        last_db_update = getattr(cls_record, "updated_at", None)
        if file_modified and last_db_update and file_modified <= last_db_update + timedelta(minutes=2):
            return "skip"

        return "reclassify"

    if getattr(cls_record, "processing_attempts", 0) and cls_record.processing_attempts > 0:
        return "reclassify"

    return "new"


async def _process_one_document(
    token: str,
    item: Dict[str, Any],
    action: str,
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    item_id = item["id"]
    name = item.get("name", item_id)
    web_url = item.get("webUrl", "")
    file_modified = _parse_sp_datetime(item.get("lastModifiedDateTime"))

    async with semaphore:
        async with async_session() as db:
            try:
                stmt = select(ClassificationRecord).where(
                    ClassificationRecord.sharepoint_item_id == item_id
                )
                if _is_postgres_db():
                    stmt = stmt.with_for_update()
                result = await db.execute(stmt)
                cls_record: Optional[ClassificationRecord] = result.scalars().first()

                if cls_record is None:
                    # Single source of truth: Always generate sequential document numbers from
                    # document_number_seq to eliminate collisions between SharePoint list item IDs and sequence numbers.
                    if _is_postgres_db():
                        from sqlalchemy import text as _text

                        seq_result = await db.execute(
                            _text("SELECT nextval('document_number_seq')")
                        )
                        doc_number: int = seq_result.scalar_one()
                    else:
                        try:
                            from sqlalchemy import text as _text

                            seq_result = await db.execute(
                                _text("SELECT nextval('document_number_seq')")
                            )
                            doc_number = int(seq_result.scalar_one())
                        except Exception:
                            doc_number = 101

                    doc_record = DocumentRecord(
                        id=str(uuid.uuid4()),
                        document_id=item_id,  # use SP item ID as the document_id key
                        name=name,
                        source="SharePoint",
                        file_type=name.rsplit(".", 1)[-1].lower() if "." in name else "pdf",
                        document_number=doc_number,
                    )
                    db.add(doc_record)
                    cls_record = ClassificationRecord(
                        id=str(uuid.uuid4()),
                        document_id=doc_record.id,
                        status="processing",
                        confidence_score=0.0,
                        sharepoint_item_id=item_id,
                        sharepoint_drive_id=SHAREPOINT_SOURCE_DRIVE_ID,
                        sharepoint_web_url=web_url,
                        processing_status="processing",
                        processing_attempts=0,
                        purview_label_applied=False,
                    )
                    db.add(cls_record)
                    await db.flush()
                else:
                    cls_record.processing_status = "processing"
                    cls_record.sharepoint_web_url = web_url
                    await db.flush()

                await db.commit()
                classification_id = cls_record.id

            except Exception as exc:
                logger.error(f"[Scheduler] DB setup failed for {name}: {exc}")
                return {
                    "item_id": item_id,
                    "name": name,
                    "action_taken": action,
                    "success": False,
                    "error": str(exc),
                }

        from app.services.document_intelligence_service import (
            analyze_and_normalize_document,
        )
        from app.services.document_understanding_service import analyze_document
        from app.services.classification_service import classify_document

        try:
            logger.info(f"[Scheduler] Downloading {name} from SharePoint...")
            file_content = await _with_backoff(
                lambda: _download_drive_item(
                    _get_current_token(token), SHAREPOINT_SOURCE_DRIVE_ID, item_id
                ),
                label=f"download:{name}",
            )

            logger.info(f"[Scheduler] OCR: {name}")
            document_profile = await _with_backoff(
                lambda: analyze_and_normalize_document(file_content, name),
                label=f"ocr:{name}",
            )

            logger.info(f"[Scheduler] Understanding: {name}")
            understanding = await asyncio.to_thread(analyze_document, document_profile)
            if not understanding:
                raise ValueError("Document understanding returned no result.")

            logger.info(f"[Scheduler] Classifying: {name}")
            classification = await asyncio.to_thread(
                classify_document, document_profile, understanding
            )

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning(
                    f"[Scheduler] {name} returned 404 — file deleted from SharePoint."
                )
                async with async_session() as db:
                    stmt = select(ClassificationRecord).where(
                        ClassificationRecord.id == classification_id
                    )
                    res = await db.execute(stmt)
                    rec = res.scalars().first()
                    if rec:
                        rec.processing_status = "source_deleted"
                        rec.processing_error = (
                            "File deleted from SharePoint before processing completed."
                        )
                        await db.commit()
                return {
                    "item_id": item_id,
                    "name": name,
                    "action_taken": "source_deleted",
                    "success": False,
                    "error": "404 source deleted",
                }
            raise  # Re-raise for the outer handler

        except Exception as exc:
            err = str(exc)
            logger.error(
                f"[Scheduler] Classification pipeline failed for {name}: {err}"
            )
            async with async_session() as db:
                stmt = select(ClassificationRecord).where(
                    ClassificationRecord.id == classification_id
                )
                res = await db.execute(stmt)
                rec = res.scalars().first()
                if rec:
                    rec.processing_attempts = (rec.processing_attempts or 0) + 1
                    rec.processing_error = err
                    if rec.processing_attempts >= MAX_PROCESSING_RETRIES:
                        rec.processing_status = "failed_permanent"
                        logger.error(
                            f"[Scheduler] {name} exceeded max retries — marked failed_permanent."
                        )
                    else:
                        rec.processing_status = "failed"
                    await db.commit()
            return {
                "item_id": item_id,
                "name": name,
                "action_taken": action,
                "success": False,
                "error": err,
            }

        async with async_session() as db:
            try:
                from sqlalchemy.orm import selectinload

                stmt = (
                    select(ClassificationRecord)
                    .where(ClassificationRecord.id == classification_id)
                    .options(selectinload(ClassificationRecord.document))
                )
                res = await db.execute(stmt)
                cls_record = res.scalars().first()

                if cls_record is None:
                    raise RuntimeError(
                        f"ClassificationRecord {classification_id} disappeared."
                    )

                old_rule = cls_record.retention_rule
                old_code = cls_record.retention_code
                old_doc_type = cls_record.document_type
                old_category = cls_record.category

                retention_rule_changed = (
                    action == "reclassify"
                    and (
                        (classification.retention_rule != old_rule)
                        or (classification.retention_code != old_code)
                    )
                )

                # Prepare audit notes showing old and new values if reclassified
                audit_notes = None
                if action == "reclassify":
                    changes = []
                    if old_rule != classification.retention_rule:
                        changes.append(
                            f"RetentionRule '{old_rule}' -> '{classification.retention_rule}'"
                        )
                    if old_doc_type != classification.document_type:
                        changes.append(
                            f"DocumentType '{old_doc_type}' -> '{classification.document_type}'"
                        )
                    if old_category != classification.category:
                        changes.append(
                            f"Category '{old_category}' -> '{classification.category}'"
                        )
                    audit_notes = (
                        f"Reclassified: {', '.join(changes)}"
                        if changes
                        else "Reclassified with identical classification"
                    )

                # Guard: Human review decisions must not be silently overwritten.
                # If a human had previously reviewed this document and the AI reclassification
                # produced a different result, preserve the human decision and queue for review.
                is_previously_reviewed = (
                    getattr(cls_record, "classified_by", None) == "human"
                    or getattr(cls_record, "reviewed_at", None) is not None
                    or getattr(cls_record, "document_tagged_status", None)
                    in ("Reviewed", "Manually Tagged")
                )
                if action == "reclassify" and is_previously_reviewed:
                    if (
                        classification.document_type != old_doc_type
                        or classification.retention_rule != old_rule
                    ):
                        logger.warning(
                            f"[Scheduler] Reclassification of previously reviewed doc {name} produced different "
                            f"result (AI: rule='{classification.retention_rule}', type='{classification.document_type}'; "
                            f"Human: rule='{old_rule}', type='{old_doc_type}'). "
                            "Preserving human decision and queuing for review."
                        )
                        cls_record.review_required = True
                        cls_record.status = "review_recommended"
                        cls_record.document_tagged_status = "Review Pending"
                        cls_record.content_changed_after_review = True
                        reasons = cls_record.review_reasons or []
                        if isinstance(reasons, list):
                            diff_msg = (
                                f"Content changed after human review. AI proposed '{classification.document_type}' "
                                f"({classification.retention_rule}) vs human decision '{old_doc_type}' ({old_rule})."
                            )
                            if diff_msg not in reasons:
                                reasons.append(diff_msg)
                            cls_record.review_reasons = reasons
                else:
                    cls_record.status = classification.classification_status
                    cls_record.confidence_score = classification.confidence_score
                    cls_record.selected_candidate_id = classification.selected_candidate_id
                    cls_record.reason = classification.reason
                    cls_record.category = classification.category
                    cls_record.section = classification.section
                    cls_record.document_type = classification.document_type
                    cls_record.retention_label = classification.retention_label
                    cls_record.team_owner = classification.team_owner
                    cls_record.classification_type = classification.classification_type
                    cls_record.retention_rule = classification.retention_rule
                    cls_record.retention_code = classification.retention_code
                    cls_record.retention_start_date = (
                        date.fromisoformat(classification.retention_start_date)
                        if isinstance(classification.retention_start_date, str) and classification.retention_start_date
                        else classification.retention_start_date
                    )
                    cls_record.retention_end_date = (
                        date.fromisoformat(classification.retention_end_date)
                        if isinstance(classification.retention_end_date, str) and classification.retention_end_date
                        else classification.retention_end_date
                    )
                    cls_record.retention_period = classification.retention_period
                    cls_record.retention_period_unit = classification.retention_period_unit
                    cls_record.lifecycle_status = classification.lifecycle_status
                    cls_record.review_required = classification.review_required
                    cls_record.review_reasons = classification.review_reasons
                    cls_record.next_action = classification.next_action
                    cls_record.trigger_condition = classification.trigger_condition

                cls_record.processing_attempts = (
                    cls_record.processing_attempts or 0
                ) + 1
                cls_record.sharepoint_last_modified = file_modified

                # If retention rule changed on reclassification, Purview label must be re-applied
                if retention_rule_changed:
                    cls_record.purview_label_applied = False

                await db.flush()

                from app.config import SHAREPOINT_RETENTION_LIST_ID

                existing_sp_list_id = cls_record.sharepoint_list_item_id
                if not existing_sp_list_id:
                    doc_num_for_check = None
                    if cls_record.document and cls_record.document.document_number:
                        doc_num_for_check = cls_record.document.document_number
                    if doc_num_for_check:
                        existing_sp_list_id = await _query_existing_list_item(
                            _get_current_token(token),
                            SHAREPOINT_SITE_ID,
                            SHAREPOINT_RETENTION_LIST_ID,
                            doc_num_for_check,
                        )
                    if existing_sp_list_id:
                        logger.info(
                            f"[Scheduler] Duplicate-check found existing SP list item "
                            f"{existing_sp_list_id} for {name} (doc#{doc_num_for_check}) "
                            f"— will PATCH instead of POST."
                        )
                        cls_record.sharepoint_list_item_id = existing_sp_list_id
                        await db.flush()

                doc_number_for_payload: Optional[int] = None
                if cls_record.document:
                    doc_number_for_payload = cls_record.document.document_number

                # Safety guard: if status is not auto_approved or review is required,
                # never treat the label as already applied — uncertain docs must not be locked.
                # Also, if retention rule changed on reclassification, force effective_label_applied = False.
                effective_label_applied = cls_record.purview_label_applied
                if retention_rule_changed:
                    effective_label_applied = False
                elif (
                    cls_record.status != "auto_approved"
                    or cls_record.review_required
                ) and effective_label_applied:
                    logger.warning(
                        f"[Scheduler] {name} — purview_label_applied=True in DB but "
                        f"status='{cls_record.status}', review_required={cls_record.review_required}. "
                        f"Overriding to False so label is NOT applied this cycle."
                    )
                    effective_label_applied = False

                payload = SyncPayload(
                    classification_record_id=cls_record.id,
                    document_name=name,
                    document_type=cls_record.document_type,
                    category=cls_record.category,
                    section=cls_record.section,
                    retention_code=cls_record.retention_code,
                    retention_rule=cls_record.retention_rule,
                    retention_end_date=str(cls_record.retention_end_date or ""),
                    confidence_score=cls_record.confidence_score,
                    team_owner=cls_record.team_owner,
                    classification_status=cls_record.status,
                    document_number=doc_number_for_payload,
                    drive_id=SHAREPOINT_SOURCE_DRIVE_ID,
                    sharepoint_item_id=item_id,
                    sharepoint_list_item_id=existing_sp_list_id
                    or cls_record.sharepoint_list_item_id,
                    sharepoint_web_url=web_url,
                    purview_label_applied=effective_label_applied,
                    triggered_by="background-scheduler",
                    audit_action="classified" if action == "new" else "reclassified",
                    audit_notes=audit_notes,
                    review_required=bool(cls_record.review_required),
                )

                sync_result = await sync_classification_to_sharepoint(
                    _get_current_token(token), payload, db
                )

                cls_record.processing_status = "completed"
                cls_record.processing_error = None
                if sync_result.sharepoint_list_item_id:
                    cls_record.sharepoint_list_item_id = (
                        sync_result.sharepoint_list_item_id
                    )

                await db.commit()
                logger.info(
                    f"[Scheduler] {name} — classification OK, "
                    f"sync={'full' if sync_result.fully_successful else 'partial'}"
                )
                return {
                    "item_id": item_id,
                    "name": name,
                    "action_taken": action,
                    "success": True,
                    "error": None,
                }

            except Exception as exc:
                err = str(exc)
                logger.error(
                    f"[Scheduler] Post-classification DB/sync error for {name}: {err}"
                )
                async with async_session() as fallback_db:
                    stmt = select(ClassificationRecord).where(
                        ClassificationRecord.id == classification_id
                    )
                    res = await fallback_db.execute(stmt)
                    rec = res.scalars().first()
                    if rec:
                        rec.processing_attempts = (rec.processing_attempts or 0) + 1
                        rec.processing_error = err
                        if rec.processing_attempts >= MAX_PROCESSING_RETRIES:
                            rec.processing_status = "failed_permanent"
                        else:
                            rec.processing_status = "failed"
                        await fallback_db.commit()
                return {
                    "item_id": item_id,
                    "name": name,
                    "action_taken": action,
                    "success": False,
                    "error": err,
                }


# Runs one complete cycle of polling and processing new files
async def _run_poll_cycle(token: Optional[str] = None) -> None:
    semaphore = asyncio.Semaphore(SYNC_BATCH_CONCURRENCY)

    try:
        items = await _with_backoff(
            lambda: _list_drive_items(_get_current_token(token), SHAREPOINT_SOURCE_DRIVE_ID),
            label="list_drive_items",
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Failed to list SharePoint documents: {exc}")
        return

    actions: List[tuple] = []
    async with async_session() as db:
        for item in items:
            try:
                action = await _determine_action(db, item)
                actions.append((item, action))
            except Exception as exc:
                logger.error(
                    f"[Scheduler] Skip-check failed for {item.get('name', item.get('id'))}: {exc}"
                )

    new_count = sum(1 for _, a in actions if a == "new")
    reclassify_count = sum(1 for _, a in actions if a in ("reclassify", "resume"))
    skip_count = sum(1 for _, a in actions if a == "skip")
    perm_skip_count = sum(1 for _, a in actions if a == "skip_permanent")

    logger.info(
        f"[Scheduler] Poll — {len(items)} files found: "
        f"{new_count} new, {reclassify_count} to reclassify/resume, "
        f"{skip_count} unchanged (skip), {perm_skip_count} permanently failed (skip)"
    )

    actionable = [
        (item, action)
        for item, action in actions
        if action not in ("skip", "skip_permanent")
    ]
    if not actionable:
        return

    tasks = [
        _process_one_document(token, item, action, semaphore)
        for item, action in actionable
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
    failed = len(results) - success
    logger.info(
        f"[Scheduler] Poll complete — {success} processed successfully, {failed} failed."
    )


async def _scheduler_loop() -> None:
    interval_seconds = SYNC_POLL_INTERVAL_MINUTES * 60
    logger.info(
        f"[Scheduler] Background polling started — interval={SYNC_POLL_INTERVAL_MINUTES}min, "
        f"concurrency={SYNC_BATCH_CONCURRENCY}, max_retries={MAX_PROCESSING_RETRIES}"
    )

    await asyncio.sleep(5)

    while not _shutdown_event.is_set():
        if _poll_lock.locked():
            logger.warning(
                "[Scheduler] Previous poll cycle still running — skipping this interval. "
                "Consider increasing SYNC_POLL_INTERVAL_MINUTES."
            )
        else:
            async with _poll_lock:
                from app.services.graph_auth_service import graph_auth_service

                if graph_auth_service is None:
                    logger.warning(
                        "[Scheduler] SharePoint auth not configured — skipping poll cycle. "
                        "Set SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID."
                    )
                elif async_session is None:
                    logger.warning(
                        "[Scheduler] Database not configured — skipping poll cycle."
                    )
                else:
                    async with _acquire_distributed_poll_lock() as got_lock:
                        if not got_lock:
                            pass  # Another worker is already polling, skip this cycle cleanly
                        else:
                            try:
                                from app.config import REVIEW_SYNC_ENABLED
                                if REVIEW_SYNC_ENABLED:
                                    from app.sync.review_writeback import run_review_writeback
                                    async with async_session() as db:
                                        wb_token = graph_auth_service.get_access_token()
                                        wb_result = await run_review_writeback(wb_token, db)
                                        if wb_result.get("applied", 0) > 0 or wb_result.get("error", 0) > 0:
                                            logger.info(f"[Scheduler] Review write-back: {wb_result}")

                                await _run_poll_cycle()

                                from app.sync.sharepoint_sync import retry_failed_syncs

                                async with async_session() as db:
                                    retry_token = graph_auth_service.get_access_token()
                                    retry_results = await retry_failed_syncs(retry_token, db)
                                    if retry_results["attempted"] > 0:
                                        logger.info(f"[Scheduler] Retry syncs: {retry_results}")
                            except Exception as exc:
                                logger.error(
                                    f"[Scheduler] Unhandled error in poll cycle: {exc}"
                                )

        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(_shutdown_event.wait())),
                timeout=interval_seconds,
            )
            break
        except asyncio.TimeoutError:
            pass  # Normal — interval elapsed, loop again

    logger.info("[Scheduler] Background polling stopped.")


_scheduler_task: Optional[asyncio.Task] = None


def start_scheduler() -> None:
    global _scheduler_task
    _shutdown_event.clear()
    _scheduler_task = asyncio.ensure_future(_scheduler_loop())
    logger.info("[Scheduler] Scheduler task created.")


async def stop_scheduler() -> None:
    global _scheduler_task
    _shutdown_event.set()
    if _scheduler_task and not _scheduler_task.done():
        try:
            await asyncio.wait_for(_scheduler_task, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(
                "[Scheduler] Shutdown timed out after 30 s — cancelling task."
            )
            _scheduler_task.cancel()
    logger.info("[Scheduler] Scheduler stopped.")
