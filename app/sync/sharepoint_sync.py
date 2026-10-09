import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    ALLOW_BASE_CODE_FALLBACK,
    PURVIEW_LABEL_NAME_MAP,
    PURVIEW_LABEL_STRATEGY,
    SHAREPOINT_AUDITLOG_LIST_ID,
    SHAREPOINT_RETENTION_LIST_ID,
    SHAREPOINT_SITE_ID,
    SHAREPOINT_SOURCE_DRIVE_ID,
)
from app.services.purview_label_resolver import normalize_retention_rule, resolve_purview_label
from app.models.db_models import ClassificationRecord, SharePointSyncLog
from app.models.sharepoint_enums import (
    DocumentTaggedStatus,
    DeletionStatus,
    validate_choice_value,
)
from app.services.validation_service import determine_document_tagged_status

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class LabelNotFoundError(Exception):

    def __init__(self, label_name: str, retention_rule: str, raw_message: str):
        self.label_name = label_name
        self.retention_rule = retention_rule
        self.raw_message = raw_message
        super().__init__(
            f"Purview label '{label_name}' (for retention_rule='{retention_rule}') not found "
            f"in tenant. Label not yet published in Purview. Raw: {raw_message}"
        )


class NoLabelMappingError(Exception):
    # Raised when the retention_rule has no entry in purview_labels.json
    def __init__(self, retention_rule: str):
        self.retention_rule = retention_rule
        super().__init__(
            f"No Purview label mapping found for retention_rule='{retention_rule}'. "
            f"Add it to app/purview_labels.json."
        )


# Formats the sequential document ID as a string
def format_document_id(document_number: int) -> str:
    return str(document_number)


class SyncPayload:

    def __init__(
        self,
        *,
        classification_record_id: str,
        document_name: str,
        document_type: Optional[str],
        category: Optional[str],
        section: Optional[str],
        retention_code: Optional[str],  # base trigger code e.g. "AL", "FE"
        retention_rule: Optional[str],  # full rule e.g. "AL+3"
        retention_end_date: Optional[str],  # ISO date string
        confidence_score: float,
        team_owner: Optional[str],
        classification_status: str,
        document_number: Optional[int],  # friendly sequential ID (101, 102, ...)
        drive_id: Optional[str],  # drive ID of POC_Source_Documents
        sharepoint_item_id: Optional[str],  # driveItem ID of the file
        sharepoint_list_item_id: Optional[
            str
        ],  # existing POC_Documents_Retention row ID
        sharepoint_web_url: Optional[str],  # webUrl for Document_Location
        purview_label_applied: bool,
        triggered_by: str = "system",
        audit_action: str = "classified",
        audit_notes: Optional[str] = None,
        force_purview_label: bool = False,  # Human review: always apply label
    ):
        self.classification_record_id = classification_record_id
        self.document_name = document_name
        self.document_type = document_type
        self.category = category
        self.section = section
        self.retention_code = retention_code
        self.retention_rule = retention_rule
        self.retention_end_date = retention_end_date
        self.confidence_score = confidence_score
        self.team_owner = team_owner
        self.classification_status = classification_status
        self.document_number = document_number
        self.drive_id = drive_id or SHAREPOINT_SOURCE_DRIVE_ID
        self.sharepoint_item_id = sharepoint_item_id
        self.sharepoint_list_item_id = sharepoint_list_item_id
        self.sharepoint_web_url = sharepoint_web_url
        self.purview_label_applied = purview_label_applied
        self.triggered_by = triggered_by
        self.audit_action = audit_action
        self.audit_notes = audit_notes
        self.force_purview_label = force_purview_label


class SyncResult:

    def __init__(self):
        self.steps_attempted: list[str] = []
        self.steps_succeeded: list[str] = []
        self.steps_failed: list[str] = []
        self.errors: dict[str, str] = {}
        self.sharepoint_list_item_id: Optional[str] = (
            None  # populated on step 3 success
        )

    @property
    def fully_successful(self) -> bool:
        return len(self.steps_failed) == 0 and len(self.steps_succeeded) > 0


async def _graph_patch(token: str, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(url, headers=headers, json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API Error: {resp.text}")
            raise e
        try:
            return resp.json()
        except Exception:
            return {}


async def _graph_post(token: str, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API Error: {resp.text}")
            raise e
        return resp.json()


async def _graph_get(token: str, url: str) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


class _RealGraphClient:

    async def patch_retention_label(
        self, token: str, drive_id: str, item_id: str, retention_rule: str
    ) -> Dict:
        # Resolve label name from the full rule (e.g. 'FE+2') using the active strategy
        if PURVIEW_LABEL_STRATEGY == "base_code":
            # Legacy: derive from base code only
            import re
            m = re.match(r"^([A-Za-z]+)", (retention_rule or "").strip())
            base = m.group(1).upper() if m else ""
            if base not in PURVIEW_LABEL_NAME_MAP:
                raise NoLabelMappingError(retention_rule)
            purview_label_name = PURVIEW_LABEL_NAME_MAP[base]
        else:
            # Default per_rule strategy
            purview_label_name = resolve_purview_label(retention_rule)
            if purview_label_name is None:
                if ALLOW_BASE_CODE_FALLBACK:
                    import re
                    m = re.match(r"^([A-Za-z]+)", (retention_rule or "").strip())
                    base = m.group(1).upper() if m else ""
                    fallback = PURVIEW_LABEL_NAME_MAP.get(base)
                    if fallback:
                        logger.warning(
                            f"[ALLOW_BASE_CODE_FALLBACK] No per_rule mapping for '{retention_rule}'. "
                            f"Falling back to base label '{fallback}'. THIS IS FOR TESTING ONLY."
                        )
                        purview_label_name = fallback
                    else:
                        raise NoLabelMappingError(retention_rule)
                else:
                    raise NoLabelMappingError(retention_rule)

        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/retentionLabel"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                url, headers=headers, json={"name": purview_label_name}
            )

            if resp.status_code == 400:
                raw = resp.text
                logger.warning(
                    f"Purview label '{purview_label_name}' not found in tenant — "
                    f"label not yet published. Raw response: {raw}"
                )
                raise LabelNotFoundError(
                    label_name=purview_label_name,
                    retention_rule=retention_rule,
                    raw_message=raw,
                )

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"Graph API Error (retentionLabel): {resp.text}")
                raise e

            try:
                return resp.json()
            except Exception:
                return {"_resolved_label": purview_label_name}

    async def patch_metadata_columns(
        self, token: str, site_id: str, drive_id: str, item_id: str, fields: Dict
    ) -> Dict:

        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/listItem/fields"
        return await _graph_patch(token, url, fields)

    async def create_retention_list_item(
        self, token: str, site_id: str, list_id: str, fields: Dict
    ) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items"
        return await _graph_post(token, url, {"fields": fields})

    async def update_retention_list_item(
        self, token: str, site_id: str, list_id: str, item_id: str, fields: Dict
    ) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items/{item_id}/fields"
        return await _graph_patch(token, url, fields)

    async def get_retention_list_item(
        self, token: str, site_id: str, list_id: str, item_id: str
    ) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items/{item_id}?$expand=fields"
        return await _graph_get(token, url)

    async def create_auditlog_item(
        self, token: str, site_id: str, list_id: str, fields: Dict
    ) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items"
        return await _graph_post(token, url, {"fields": fields})


def _get_graph_client():
    return _RealGraphClient()


async def _log_sync(
    db: AsyncSession,
    classification_record_id: str,
    sync_type: str,
    status: str,
    error_message: Optional[str] = None,
    completed_at: Optional[datetime] = None,
    error_type: Optional[str] = None,
) -> None:
    full_message = error_message
    if error_type and error_message:
        full_message = f"[{error_type}] {error_message}"
    elif error_type:
        full_message = f"[{error_type}]"

    log = SharePointSyncLog(
        id=str(uuid.uuid4()),
        classification_record_id=classification_record_id,
        sync_type=sync_type,
        status=status,
        error_message=full_message,
        attempted_at=datetime.now(timezone.utc),
        completed_at=completed_at
        or (datetime.now(timezone.utc) if status == "success" else None),
    )
    db.add(log)
    await db.flush()


async def sync_classification_to_sharepoint(
    token: str,
    payload: SyncPayload,
    db: AsyncSession,
) -> SyncResult:
    graph = _get_graph_client()
    result = SyncResult()
    now = datetime.now(timezone.utc)

    site_id = SHAREPOINT_SITE_ID
    drive_id = payload.drive_id
    retention_list_id = SHAREPOINT_RETENTION_LIST_ID
    auditlog_list_id = SHAREPOINT_AUDITLOG_LIST_ID

    if not all([site_id, drive_id, retention_list_id, auditlog_list_id]):
        logger.error(
            "SharePoint sync cannot run: missing one or more required env vars "
            "(SHAREPOINT_SITE_ID, SHAREPOINT_SOURCE_DRIVE_ID, "
            "SHAREPOINT_RETENTION_LIST_ID, SHAREPOINT_AUDITLOG_LIST_ID)"
        )
        return result

    item_id = payload.sharepoint_item_id
    if not item_id:
        logger.error(
            "sync_classification_to_sharepoint: sharepoint_item_id is required but not set."
        )
        return result

    step = "retention_label"
    result.steps_attempted.append(step)

    if payload.purview_label_applied:
        logger.info(
            f"[Sync] Step 1 skipped — Purview label already applied for "
            f"{payload.classification_record_id}"
        )
        result.steps_succeeded.append(step)

    elif payload.classification_status != "auto_approved" and not payload.force_purview_label:
        logger.info(
            f"[Sync] Step 1 skipped — Document is pending review (Confidence < Threshold). "
            f"Purview label will NOT be applied yet."
        )
        result.steps_succeeded.append(step)

    elif payload.retention_rule:
        resolved_label = resolve_purview_label(payload.retention_rule) if PURVIEW_LABEL_STRATEGY == "per_rule" else PURVIEW_LABEL_NAME_MAP.get(payload.retention_code or "", "<unmapped>")
        logger.info(
            f"[Sync] Step 1 — doc #{payload.document_number} | "
            f"retention_rule='{payload.retention_rule}' | "
            f"resolved_label='{resolved_label}' | "
            f"strategy='{PURVIEW_LABEL_STRATEGY}'"
        )
        try:
            await graph.patch_retention_label(
                token, drive_id, item_id, payload.retention_rule
            )
            await _log_sync(db, payload.classification_record_id, step, "success")
            result.steps_succeeded.append(step)
            logger.info(
                f"[Sync] Step 1 OK — Purview label '{resolved_label}' "
                f"(retention_rule='{payload.retention_rule}') applied to item {item_id}"
            )

            cls_record = await db.get(
                ClassificationRecord, payload.classification_record_id
            )
            if cls_record:
                cls_record.purview_label_applied = True
                cls_record.purview_label_applied_at = datetime.now(timezone.utc)
                await db.flush()

        except NoLabelMappingError as nme:
            err = str(nme)
            logger.error(
                f"[Sync] Step 1 FAILED (no_label_mapping) — "
                f"No mapping for rule '{nme.retention_rule}' in app/purview_labels.json. "
                f"Add the mapping or create the label in Purview, then retry."
            )
            await _log_sync(
                db,
                payload.classification_record_id,
                step,
                "failed",
                error_message=err,
                error_type="no_label_mapping",
            )
            result.steps_failed.append(step)
            result.errors[step] = err

        except LabelNotFoundError as lnf:
            err = str(lnf)
            logger.error(
                f"[Sync] Step 1 FAILED (label_not_found) — "
                f"Purview label '{lnf.label_name}' not yet published in tenant. "
                f"Publish it in the Purview portal; the next retry will apply it automatically."
            )
            await _log_sync(
                db,
                payload.classification_record_id,
                step,
                "failed",
                error_message=err,
                error_type="label_not_found",
            )
            result.steps_failed.append(step)
            result.errors[step] = err

        except Exception as exc:
            err = str(exc)
            await _log_sync(
                db, payload.classification_record_id, step, "failed", error_message=err
            )
            result.steps_failed.append(step)
            result.errors[step] = err
            logger.error(f"[Sync] Step 1 FAILED — {err}")

    else:
        logger.warning(
            f"[Sync] Step 1 skipped — no retention_rule on payload. Purview label not applied."
        )

    step = "metadata_columns"
    result.steps_attempted.append(step)
    try:
        metadata_fields = {
            "DocumentCategory": payload.category,
            "DocumentType": payload.document_type,
            "RetentionCode": payload.retention_code,
            "RetentionRule": payload.retention_rule,
            "RetentionEndDate": payload.retention_end_date,
            "ConfidenceScore": float(payload.confidence_score),
            "TeamOwner": payload.team_owner,
            "ClassificationStatus": payload.classification_status,
        }
        metadata_fields = {
            k: v for k, v in metadata_fields.items() if v not in ("", None)
        }
        await graph.patch_metadata_columns(
            token, site_id, drive_id, item_id, metadata_fields
        )
        await _log_sync(db, payload.classification_record_id, step, "success")
        result.steps_succeeded.append(step)
        logger.info(f"[Sync] Step 2 OK — metadata columns written for item {item_id}")
    except Exception as exc:
        err = str(exc)
        await _log_sync(
            db, payload.classification_record_id, step, "failed", error_message=err
        )
        result.steps_failed.append(step)
        result.errors[step] = err
        logger.error(f"[Sync] Step 2 FAILED — {err}")

    step = "retention_list_item"
    result.steps_attempted.append(step)
    try:
        existing_list_item_id = payload.sharepoint_list_item_id
        confidence_pct = round(payload.confidence_score * 100, 2)

        if existing_list_item_id:
            try:
                existing = await graph.get_retention_list_item(
                    token, site_id, retention_list_id, existing_list_item_id
                )
                existing_fields = existing.get("fields", {})
            except Exception:
                existing_fields = {}

            update_fields: Dict[str, Any] = {
                "ClassificationStatus": payload.classification_status,
                "DocumentType": payload.document_type,
                "RetentionRule": payload.retention_rule,
                "RetentionCode": payload.retention_code,
                "ExpirationDate": payload.retention_end_date,
                "TeamOwner": payload.team_owner,
                "ConfidenceScore": float(payload.confidence_score),
                "DocumentURL": payload.sharepoint_web_url,
                "DocumentID": (
                    format_document_id(payload.document_number)
                    if payload.document_number
                    else None
                ),
            }

            current_tagged = existing_fields.get("DocumentTagged") or ""
            if current_tagged not in DocumentTaggedStatus.human_values():
                new_tagged_value = determine_document_tagged_status(
                    payload.classification_status
                )
                validate_choice_value(new_tagged_value, DocumentTaggedStatus)
                update_fields["DocumentTagged"] = new_tagged_value
                update_fields["DocumentTaggedDate"] = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
                logger.info(
                    f"[Sync] Step 3 PATCH — updating DocumentTagged from {current_tagged!r} "
                    f"to {new_tagged_value!r}"
                )
            else:
                logger.info(
                    f"[Sync] Step 3 PATCH — DocumentTagged is {current_tagged!r} (human-set), "
                    "not overwriting."
                )

            update_fields = {
                k: v for k, v in update_fields.items() if v not in ("", None)
            }

            await graph.update_retention_list_item(
                token, site_id, retention_list_id, existing_list_item_id, update_fields
            )
            result.sharepoint_list_item_id = existing_list_item_id
            logger.info(
                f"[Sync] Step 3 OK — PATCH existing list item {existing_list_item_id}"
            )

        else:
            doc_tagged_value = determine_document_tagged_status(
                payload.classification_status
            )
            validate_choice_value(doc_tagged_value, DocumentTaggedStatus)

            deletion_value = DeletionStatus.NOT_DELETED.value
            validate_choice_value(deletion_value, DeletionStatus)

            new_fields: Dict[str, Any] = {
                "Title": payload.document_name,
                "DocumentID": (
                    format_document_id(payload.document_number)
                    if payload.document_number
                    else None
                ),
                "ClassificationStatus": payload.classification_status,
                "DocumentType": payload.document_type,
                "RetentionRule": payload.retention_rule,
                "RetentionCode": payload.retention_code,
                "ExpirationDate": payload.retention_end_date,
                "TeamOwner": payload.team_owner,
                "ConfidenceScore": float(payload.confidence_score),
                "DocumentURL": payload.sharepoint_web_url,
                "DocumentTagged": doc_tagged_value,
                "DocumentTaggedDate": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "isDeleted": deletion_value,
            }
            new_fields = {k: v for k, v in new_fields.items() if v not in ("", None)}
            created = await graph.create_retention_list_item(
                token, site_id, retention_list_id, new_fields
            )
            new_id = created.get("id")
            result.sharepoint_list_item_id = new_id
            logger.info(
                f"[Sync] Step 3 OK — created new list item {new_id} (DocumentTagged={doc_tagged_value!r})"
            )

            cls_record = await db.get(
                ClassificationRecord, payload.classification_record_id
            )
            if cls_record and new_id:
                cls_record.sharepoint_list_item_id = new_id
                cls_record.document_tagged_status = doc_tagged_value
                cls_record.deletion_status = deletion_value
                await db.flush()

        await _log_sync(db, payload.classification_record_id, step, "success")
        result.steps_succeeded.append(step)

    except Exception as exc:
        err = str(exc)
        await _log_sync(
            db, payload.classification_record_id, step, "failed", error_message=err
        )
        result.steps_failed.append(step)
        result.errors[step] = err
        logger.error(f"[Sync] Step 3 FAILED — {err}")

    step = "audit_log_entry"
    result.steps_attempted.append(step)
    try:
        audit_fields: Dict[str, Any] = {
            "Title": payload.document_name,
            "DocumentID": (
                format_document_id(payload.document_number)
                if payload.document_number
                else None
            ),
            "Action": payload.audit_action,
            "TriggeredBy": payload.triggered_by,
            "Details": (
                payload.audit_notes
                or f"Classification processed. Status: {payload.classification_status}. "
                f"DocumentTagged: {determine_document_tagged_status(payload.classification_status)}"
            ),
            "ClassificationStatus": payload.classification_status,
            "ConfidenceScore": str(payload.confidence_score),
            "DocumentCategory": payload.category,
            "RetentionCode": payload.retention_code,
        }
        audit_fields = {k: v for k, v in audit_fields.items() if v not in ("", None)}
        await graph.create_auditlog_item(token, site_id, auditlog_list_id, audit_fields)
        await _log_sync(db, payload.classification_record_id, step, "success")
        result.steps_succeeded.append(step)
        logger.info(f"[Sync] Step 4 OK — audit log entry appended")
    except Exception as exc:
        err = str(exc)
        await _log_sync(
            db, payload.classification_record_id, step, "failed", error_message=err
        )
        result.steps_failed.append(step)
        result.errors[step] = err
        logger.error(f"[Sync] Step 4 FAILED — {err}")

    if result.fully_successful:
        cls_record = await db.get(
            ClassificationRecord, payload.classification_record_id
        )
        if cls_record:
            cls_record.sharepoint_synced_at = datetime.now(timezone.utc)
            await db.flush()
        logger.info(
            f"[Sync] Full sync SUCCESS for classification {payload.classification_record_id} "
            f"({len(result.steps_succeeded)} steps)"
        )
    else:
        logger.warning(
            f"[Sync] Partial sync for classification {payload.classification_record_id} — "
            f"succeeded: {result.steps_succeeded}, failed: {result.steps_failed}"
        )

    return result


# Scans the database for failed sync logs and re-attempts pushing them to SharePoint
async def retry_failed_syncs(
    token: str,
    db: AsyncSession,
    max_retries: int = 3,
) -> Dict[str, Any]:
    from sqlalchemy import select, cast, String

    failed_logs_stmt = (
        select(SharePointSyncLog)
        .where(cast(SharePointSyncLog.status, String) == "failed")
        .distinct(SharePointSyncLog.classification_record_id)
    )
    result = await db.execute(failed_logs_stmt)
    failed_logs = result.scalars().all()

    attempted = 0
    recovered = 0
    still_failing = 0

    from sqlalchemy.orm import selectinload

    for log in failed_logs:
        cls_record = await db.get(
            ClassificationRecord,
            log.classification_record_id,
            options=[selectinload(ClassificationRecord.document)],
        )
        if not cls_record:
            continue

        log.status = "retrying"
        await db.flush()

        payload = SyncPayload(
            classification_record_id=cls_record.id,
            document_name=(
                cls_record.document.name if cls_record.document else "Unknown"
            ),
            document_type=cls_record.document_type,
            category=cls_record.category,
            section=cls_record.section,
            retention_code=cls_record.retention_code,
            retention_rule=cls_record.retention_rule,
            retention_end_date=cls_record.retention_end_date,
            confidence_score=cls_record.confidence_score,
            team_owner=cls_record.team_owner,
            classification_status=cls_record.status,
            document_number=(
                cls_record.document.document_number if cls_record.document else None
            ),
            drive_id=cls_record.sharepoint_drive_id,
            sharepoint_item_id=cls_record.sharepoint_item_id,
            sharepoint_list_item_id=cls_record.sharepoint_list_item_id,
            sharepoint_web_url=cls_record.sharepoint_web_url,
            purview_label_applied=cls_record.purview_label_applied,
            triggered_by="system-retry",
            audit_action="retry_sync",
            audit_notes=f"Automatic retry attempt",
        )

        sync_result = await sync_classification_to_sharepoint(token, payload, db)
        attempted += 1
        if sync_result.fully_successful:
            recovered += 1
        else:
            still_failing += 1

    await db.commit()
    return {
        "attempted": attempted,
        "recovered": recovered,
        "still_failing": still_failing,
    }
