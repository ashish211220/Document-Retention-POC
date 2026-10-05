"""
SharePoint Sync Module — app/sync/sharepoint_sync.py

Pushes a completed classification result out to the three SharePoint structures
that Power Apps reads for the human review/deletion workflow.

SharePoint structures (already created manually):
  1. POC_Source_Documents       — Document Library  (driveItem metadata + Purview label)
  2. POC_Documents_Retention    — List              (one row per document, full metadata)
  3. POC_classification_auditlog — List             (append-only action history)

Sync order (must be preserved — later steps depend on earlier ones):
  Step 1 — Apply Purview retention label via PATCH /retentionLabel (dedicated endpoint)
  Step 2 — Write custom metadata columns on the driveItem via PATCH /listItem/fields
  Step 3 — Create or update the row in POC_Documents_Retention
  Step 4 — Append a row to POC_classification_auditlog

Each step is logged independently to the `sharepoint_sync_logs` table so that
partial failures can be retried without re-running classification or duplicating rows.

Retry safety:
  - Step 1: guarded by `purview_label_applied` flag — skip if already True.
  - Step 3: guarded by `sharepoint_list_item_id` — PATCH existing row vs POST new one.
            `isDeleted` and `Deletion_Approved_By` are NEVER overwritten once set
            (ownership belongs to Power Apps / human reviewer).
  - Steps 2, 4: idempotent PATCH/POST — safe to retry.

BUSINESS CONSTRAINT (do NOT change):
  This module is called AFTER the classification decision has already been made
  and persisted to PostgreSQL. It must not touch classification logic, confidence
  scores, taxonomy lookups, or OCR/OpenAI processing in any way.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    PURVIEW_LABEL_NAME_MAP,
    SHAREPOINT_AUDITLOG_LIST_ID,
    SHAREPOINT_RETENTION_LIST_ID,
    SHAREPOINT_SITE_ID,
    SHAREPOINT_SOURCE_DRIVE_ID,
)
from app.models.db_models import ClassificationRecord, SharePointSyncLog
from app.models.sharepoint_enums import (
    DocumentTaggedStatus,
    DeletionStatus,
    validate_choice_value,
)
from app.services.validation_service import determine_document_tagged_status

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------------------
# Custom exception for Purview label-not-found failures
# ---------------------------------------------------------------------------


class LabelNotFoundError(Exception):
    """
    Raised when the Graph /retentionLabel endpoint returns a 400 response
    indicating the label name does not exist in the Purview tenant.

    This is a distinct, retryable failure: once the label is created in Purview
    and propagated, the next scheduled retry will succeed without re-running
    classification.

    Not to be confused with:
      - 401/403 — permission errors (auth bug)
      - 404     — file not found in SharePoint (source_deleted)
      - 429     — rate limit (handled by backoff in scheduler)
    """

    def __init__(self, label_name: str, retention_code: str, raw_message: str):
        self.label_name = label_name
        self.retention_code = retention_code
        self.raw_message = raw_message
        super().__init__(
            f"Purview label '{label_name}' (for retention_code='{retention_code}') not found "
            f"in tenant. Label not yet created in Purview. Raw: {raw_message}"
        )


# ---------------------------------------------------------------------------
# Document ID formatting — single point of truth for the friendly number
# ---------------------------------------------------------------------------

def format_document_id(document_number: int) -> str:
    """
    Returns the SharePoint DocumentID value for a given document_number.

    Returned as a string because the SharePoint DocumentID column is now a Text
    field. Passing an integer directly causes Graph API to randomly fail with a
    500 Internal Server Error instead of casting it.
    """
    return str(document_number)

# ---------------------------------------------------------------------------
# Data transfer object
# ---------------------------------------------------------------------------


class SyncPayload:
    """
    Carries all the data needed for a sync run.
    Built by the caller (documents.py / sharepoint.py) from the existing
    ClassificationRecord + DocumentRecord — no AI calls happen here.
    """

    def __init__(
        self,
        *,
        classification_record_id: str,
        document_name: str,
        document_type: Optional[str],
        category: Optional[str],
        section: Optional[str],
        retention_code: Optional[str],          # base trigger code e.g. "AL", "FE"
        retention_rule: Optional[str],          # full rule e.g. "AL+3"
        retention_end_date: Optional[str],      # ISO date string
        confidence_score: float,
        team_owner: Optional[str],
        classification_status: str,
        document_number: Optional[int],         # friendly sequential ID (101, 102, ...)
        # SharePoint item details — populated once the file exists in SP
        drive_id: Optional[str],                # drive ID of POC_Source_Documents
        sharepoint_item_id: Optional[str],      # driveItem ID of the file
        sharepoint_list_item_id: Optional[str], # existing POC_Documents_Retention row ID
        sharepoint_web_url: Optional[str],      # webUrl for Document_Location
        purview_label_applied: bool,
        # Audit context
        triggered_by: str = "system",           # e.g. "system", "reviewer@org.com"
        audit_action: str = "classified",       # e.g. "classified", "reclassified", "legal_hold_applied"
        audit_notes: Optional[str] = None,
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


class SyncResult:
    """Aggregated result of a full sync run."""

    def __init__(self):
        self.steps_attempted: list[str] = []
        self.steps_succeeded: list[str] = []
        self.steps_failed: list[str] = []
        self.errors: dict[str, str] = {}
        self.sharepoint_list_item_id: Optional[str] = None  # populated on step 3 success

    @property
    def fully_successful(self) -> bool:
        return len(self.steps_failed) == 0 and len(self.steps_succeeded) > 0


# ---------------------------------------------------------------------------
# Internal Graph API helpers
# ---------------------------------------------------------------------------


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
    """Performs real Graph API calls for all sync steps."""

    async def patch_retention_label(self, token: str, drive_id: str, item_id: str, retention_code: str) -> Dict:
        """
        SPECIAL CASE: Purview retention label must be set via the dedicated
        /retentionLabel endpoint, NOT the regular fields PATCH.
        This is what makes Purview enforce the file as a record.

        Resolves retention_code → actual Purview label name via PURVIEW_LABEL_NAME_MAP
        before calling Graph. The mapped name is ONLY used here — it must never leak
        into metadata columns or any other SharePoint field.

        Raises:
            LabelNotFoundError: When Graph returns 400 indicating the label name does
                not exist yet in the Purview tenant (e.g. CE_V1, AC_V1 not yet created).
            httpx.HTTPStatusError: For all other HTTP errors (401, 403, 404, 429, 5xx).
            KeyError: If retention_code has no entry in PURVIEW_LABEL_NAME_MAP at all.
        """
        # --- Resolve to real Purview label name (single lookup point) ---
        if retention_code not in PURVIEW_LABEL_NAME_MAP:
            raise KeyError(
                f"retention_code '{retention_code}' has no entry in PURVIEW_LABEL_NAME_MAP. "
                f"Add it to app/config.py before syncing."
            )
        purview_label_name = PURVIEW_LABEL_NAME_MAP[retention_code]

        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/retentionLabel"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(url, headers=headers, json={"name": purview_label_name})

            # 400 specifically means the label name is unknown to Purview.
            # Distinguish this from all other HTTP errors so callers can handle it
            # as a retryable "waiting for admin to create label" state.
            if resp.status_code == 400:
                raw = resp.text
                logger.warning(
                    f"Purview label '{purview_label_name}' not found — label not yet created "
                    f"in Purview. Raw response: {raw}"
                )
                raise LabelNotFoundError(
                    label_name=purview_label_name,
                    retention_code=retention_code,
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
                return {}

    async def patch_metadata_columns(self, token: str, site_id: str, drive_id: str, item_id: str, fields: Dict) -> Dict:
        """
        Updates custom metadata columns on the driveItem in POC_Source_Documents.
        NOTE: do NOT include 'Retention_label' here — that is handled by
        patch_retention_label above via the dedicated /retentionLabel endpoint.
        """
        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/listItem/fields"
        return await _graph_patch(token, url, fields)

    async def create_retention_list_item(self, token: str, site_id: str, list_id: str, fields: Dict) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items"
        return await _graph_post(token, url, {"fields": fields})

    async def update_retention_list_item(self, token: str, site_id: str, list_id: str, item_id: str, fields: Dict) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items/{item_id}/fields"
        return await _graph_patch(token, url, fields)

    async def get_retention_list_item(self, token: str, site_id: str, list_id: str, item_id: str) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items/{item_id}?$expand=fields"
        return await _graph_get(token, url)

    async def create_auditlog_item(self, token: str, site_id: str, list_id: str, fields: Dict) -> Dict:
        url = f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items"
        return await _graph_post(token, url, {"fields": fields})


def _get_graph_client():
    return _RealGraphClient()


# ---------------------------------------------------------------------------
# Sync log helpers
# ---------------------------------------------------------------------------


async def _log_sync(
    db: AsyncSession,
    classification_record_id: str,
    sync_type: str,
    status: str,
    error_message: Optional[str] = None,
    completed_at: Optional[datetime] = None,
    error_type: Optional[str] = None,
) -> None:
    """
    Write a SharePointSyncLog row.

    error_type values (when status='failed'):
      'label_not_found'  — Purview label name not yet created in tenant; retryable.
      None               — generic failure; inspect error_message for details.

    The error_type is serialised as a structured prefix in error_message so it
    is queryable with a LIKE filter even without a dedicated column migration:
      '[label_not_found] Purview label ...'
    """
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
        completed_at=completed_at or (datetime.now(timezone.utc) if status == "success" else None),
    )
    db.add(log)
    await db.flush()


# ---------------------------------------------------------------------------
# Public API — single entry point
# ---------------------------------------------------------------------------


async def sync_classification_to_sharepoint(
    token: str,
    payload: SyncPayload,
    db: AsyncSession,
) -> SyncResult:
    """
    Orchestrates all four SharePoint write steps for a newly classified document.

    Steps run IN ORDER. Each is independently logged to sharepoint_sync_logs.
    A failed step does NOT prevent subsequent steps from attempting (best-effort),
    but the caller can inspect SyncResult.steps_failed to decide whether to retry.

    Idempotency contract:
      - Step 1 is skipped if payload.purview_label_applied is already True.
      - Step 3 PATCHes an existing list item if payload.sharepoint_list_item_id is set,
        and guards against overwriting isDeleted / Deletion_Approved_By once populated.
      - Retry jobs should pass the current ClassificationRecord state so guards work.

    Args:
        token:   Valid Graph API bearer token with Sites.FullControl.All (or mock).
        payload: SyncPayload built from the current ClassificationRecord + DocumentRecord.
        db:      Active AsyncSession — caller owns the transaction boundary.

    Returns:
        SyncResult detailing which steps succeeded/failed.
    """
    graph = _get_graph_client()
    result = SyncResult()
    now = datetime.now(timezone.utc)

    site_id = SHAREPOINT_SITE_ID
    drive_id = payload.drive_id
    retention_list_id = SHAREPOINT_RETENTION_LIST_ID
    auditlog_list_id = SHAREPOINT_AUDITLOG_LIST_ID

    # Validate required configuration
    if not all([site_id, drive_id, retention_list_id, auditlog_list_id]):
        logger.error(
            "SharePoint sync cannot run: missing one or more required env vars "
            "(SHAREPOINT_SITE_ID, SHAREPOINT_SOURCE_DRIVE_ID, "
            "SHAREPOINT_RETENTION_LIST_ID, SHAREPOINT_AUDITLOG_LIST_ID)"
        )
        return result

    item_id = payload.sharepoint_item_id
    if not item_id:
        logger.error("sync_classification_to_sharepoint: sharepoint_item_id is required but not set.")
        return result

    # ------------------------------------------------------------------
    # Step 1: Apply Purview retention label (dedicated /retentionLabel endpoint)
    # ------------------------------------------------------------------
    step = "retention_label"
    result.steps_attempted.append(step)

    if payload.purview_label_applied:
        logger.info(
            f"[Sync] Step 1 skipped — Purview label already applied for "
            f"{payload.classification_record_id}"
        )
        result.steps_succeeded.append(step)

    elif payload.classification_status != "auto_approved":
        logger.info(
            f"[Sync] Step 1 skipped — Document is pending review (Confidence < Threshold). "
            f"Purview label will NOT be applied yet."
        )
        result.steps_succeeded.append(step)

    elif payload.retention_code:
        # Look up what we will send BEFORE calling Graph, so we can log the mapped name.
        purview_label_name = PURVIEW_LABEL_NAME_MAP.get(payload.retention_code, "<unmapped>")
        try:
            await graph.patch_retention_label(token, drive_id, item_id, payload.retention_code)
            await _log_sync(db, payload.classification_record_id, step, "success")
            result.steps_succeeded.append(step)
            logger.info(
                f"[Sync] Step 1 OK — Purview label '{purview_label_name}' "
                f"(retention_code='{payload.retention_code}') applied to item {item_id}"
            )

            # Update the ClassificationRecord to reflect the Purview label was applied
            cls_record = await db.get(ClassificationRecord, payload.classification_record_id)
            if cls_record:
                cls_record.purview_label_applied = True
                cls_record.purview_label_applied_at = datetime.now(timezone.utc)
                await db.flush()

        except LabelNotFoundError as lnf:
            # ----------------------------------------------------------------
            # Label not yet created in Purview (e.g. CE_V1, AC_V1).
            # This is a RETRYABLE failure — once the label is created and
            # propagated in Purview, the next scheduled retry will succeed here
            # without re-running classification (purview_label_applied stays
            # False so the step-1 idempotency guard will attempt it again).
            # ----------------------------------------------------------------
            err = str(lnf)
            logger.error(
                f"[Sync] Step 1 FAILED (label_not_found) — "
                f"Purview label '{lnf.label_name}' not found. "
                f"Document {payload.classification_record_id} left unlabeled. "
                f"Create '{lnf.label_name}' in the Purview portal and the next "
                f"retry will succeed automatically."
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
            # purview_label_applied stays False — do NOT set it True.
            # Continue to steps 2–4 (best-effort, same as any other partial failure).

        except Exception as exc:
            err = str(exc)
            await _log_sync(db, payload.classification_record_id, step, "failed", error_message=err)
            result.steps_failed.append(step)
            result.errors[step] = err
            logger.error(f"[Sync] Step 1 FAILED — {err}")

    else:
        logger.warning(
            f"[Sync] Step 1 skipped — no retention_code on payload. Purview label not applied."
        )

    # ------------------------------------------------------------------
    # Step 2: Write custom metadata columns on POC_Source_Documents driveItem
    # NOTE: Retention_label is intentionally excluded from this PATCH.
    # That field is a built-in Purview column set exclusively by Step 1.
    #
    # BUSINESS QUESTION (flagged for business owner confirmation before go-live):
    #   'Category' vs 'Document_Category_Name' — per spec, Category maps to
    #   retention_code (the short trigger code, e.g. "AL") and Document_Category_Name
    #   maps to the full category name (e.g. "Financial Reporting"). However, it is
    #   NOT yet confirmed whether these are truly two distinct concepts or if one is
    #   a leftover field from schema design. DO NOT remove this comment until the
    #   business owner confirms the mapping at go-live review.
    # ------------------------------------------------------------------
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
        metadata_fields = {k: v for k, v in metadata_fields.items() if v not in ("", None)}
        await graph.patch_metadata_columns(token, site_id, drive_id, item_id, metadata_fields)
        await _log_sync(db, payload.classification_record_id, step, "success")
        result.steps_succeeded.append(step)
        logger.info(f"[Sync] Step 2 OK — metadata columns written for item {item_id}")
    except Exception as exc:
        err = str(exc)
        await _log_sync(db, payload.classification_record_id, step, "failed", error_message=err)
        result.steps_failed.append(step)
        result.errors[step] = err
        logger.error(f"[Sync] Step 2 FAILED — {err}")

    # ------------------------------------------------------------------
    # Step 3: Create or update the row in POC_Documents_Retention list
    # Ownership rule: isDeleted and Deletion_Approved_By are owned by
    # the Power Apps / human reviewer side. Our backend sets isDeleted=False
    # on initial creation only, and must NEVER overwrite either field if
    # they have already been set by a human.
    # ------------------------------------------------------------------
    step = "retention_list_item"
    result.steps_attempted.append(step)
    try:
        existing_list_item_id = payload.sharepoint_list_item_id
        confidence_pct = round(payload.confidence_score * 100, 2)

        if existing_list_item_id:
            # --- Reclassification / resync: PATCH the existing row ---
            # First, fetch current values to honour ownership rules
            try:
                existing = await graph.get_retention_list_item(
                    token, site_id, retention_list_id, existing_list_item_id
                )
                existing_fields = existing.get("fields", {})
            except Exception:
                existing_fields = {}

            update_fields: Dict[str, Any] = {
                "ClassificationStatus": payload.classification_status,
                "DocumentCategory": payload.category,
                "RetentionRule": payload.retention_rule,
                "RetentionCode": payload.retention_code,
                "ExpirationDate": payload.retention_end_date,
                "TeamOwner": payload.team_owner,
                "ConfidenceScore": float(payload.confidence_score),
                "DocumentURL": payload.sharepoint_web_url,
                # DocumentID: always write the same friendly number on every resync.
                # It never changes, so writing it again on PATCH is safe and idempotent.
                "DocumentID": format_document_id(payload.document_number) if payload.document_number else None,
            }

            # DocumentTagged: only overwrite if the current SP value is still a
            # backend-owned value ('Auto-Tagged' or 'Review Pending' or empty).
            # If a human has set 'Reviewed' or 'Manually Tagged', we must NEVER
            # overwrite that — human values always win.
            current_tagged = existing_fields.get("DocumentTagged") or ""
            if current_tagged not in DocumentTaggedStatus.human_values():
                new_tagged_value = determine_document_tagged_status(payload.classification_status)
                validate_choice_value(new_tagged_value, DocumentTaggedStatus)
                update_fields["DocumentTagged"] = new_tagged_value
                update_fields["DocumentTaggedDate"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                logger.info(
                    f"[Sync] Step 3 PATCH — updating DocumentTagged from {current_tagged!r} "
                    f"to {new_tagged_value!r}"
                )
            else:
                logger.info(
                    f"[Sync] Step 3 PATCH — DocumentTagged is {current_tagged!r} (human-set), "
                    "not overwriting."
                )

            # isDeleted: ALWAYS excluded from PATCH — owned entirely by Power Apps.
            # (It was set to 'Not Deleted' on initial creation and must not be reset.)

            update_fields = {k: v for k, v in update_fields.items() if v not in ("", None)}

            await graph.update_retention_list_item(
                token, site_id, retention_list_id, existing_list_item_id, update_fields
            )
            result.sharepoint_list_item_id = existing_list_item_id
            logger.info(f"[Sync] Step 3 OK — PATCH existing list item {existing_list_item_id}")

        else:
            # --- First sync: POST a new row ---
            # Determine DocumentTagged choice value based on classification outcome.
            doc_tagged_value = determine_document_tagged_status(payload.classification_status)
            validate_choice_value(doc_tagged_value, DocumentTaggedStatus)

            # isDeleted: backend writes 'Not Deleted' on creation only.
            deletion_value = DeletionStatus.NOT_DELETED.value
            validate_choice_value(deletion_value, DeletionStatus)

            new_fields: Dict[str, Any] = {
                "Title": payload.document_name,
                # DocumentID: friendly sequential number (101, 102, ...) as an integer.
                # The Graph item ID is kept in sharepoint_item_id in PostgreSQL and
                # the file URL is in DocumentURL — no need to put the Graph ID here.
                "DocumentID": format_document_id(payload.document_number) if payload.document_number else None,
                "ClassificationStatus": payload.classification_status,
                "DocumentCategory": payload.category,
                "RetentionRule": payload.retention_rule,
                "RetentionCode": payload.retention_code,
                "ExpirationDate": payload.retention_end_date,
                "TeamOwner": payload.team_owner,
                "ConfidenceScore": float(payload.confidence_score),
                "DocumentURL": payload.sharepoint_web_url,
                "DocumentTagged": doc_tagged_value,
                "DocumentTaggedDate": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "isDeleted": deletion_value,
            }
            new_fields = {k: v for k, v in new_fields.items() if v not in ("", None)}
            created = await graph.create_retention_list_item(token, site_id, retention_list_id, new_fields)
            new_id = created.get("id")
            result.sharepoint_list_item_id = new_id
            logger.info(f"[Sync] Step 3 OK — created new list item {new_id} (DocumentTagged={doc_tagged_value!r})")

            # Persist the list item ID and SP-facing statuses back to ClassificationRecord
            cls_record = await db.get(ClassificationRecord, payload.classification_record_id)
            if cls_record and new_id:
                cls_record.sharepoint_list_item_id = new_id
                cls_record.document_tagged_status = doc_tagged_value
                cls_record.deletion_status = deletion_value
                await db.flush()

        await _log_sync(db, payload.classification_record_id, step, "success")
        result.steps_succeeded.append(step)

    except Exception as exc:
        err = str(exc)
        await _log_sync(db, payload.classification_record_id, step, "failed", error_message=err)
        result.steps_failed.append(step)
        result.errors[step] = err
        logger.error(f"[Sync] Step 3 FAILED — {err}")

    # ------------------------------------------------------------------
    # Step 4: Append a row to POC_classification_auditlog
    # Mirrors the internal AuditLog event. One row per state-changing event.
    # ------------------------------------------------------------------
    step = "audit_log_entry"
    result.steps_attempted.append(step)
    try:
        audit_fields: Dict[str, Any] = {
            "Title": payload.document_name,
            # DocumentID: same friendly number as in the retention list so audit rows
            # join cleanly to retention rows by number, not by opaque Graph item ID.
            "DocumentID": format_document_id(payload.document_number) if payload.document_number else None,
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
        await _log_sync(db, payload.classification_record_id, step, "failed", error_message=err)
        result.steps_failed.append(step)
        result.errors[step] = err
        logger.error(f"[Sync] Step 4 FAILED — {err}")

    # ------------------------------------------------------------------
    # Finalize: mark sharepoint_synced_at on the ClassificationRecord
    # only if ALL attempted steps succeeded.
    # ------------------------------------------------------------------
    if result.fully_successful:
        cls_record = await db.get(ClassificationRecord, payload.classification_record_id)
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


# ---------------------------------------------------------------------------
# Retry helper — call this from a background task / scheduler
# ---------------------------------------------------------------------------


async def retry_failed_syncs(
    token: str,
    db: AsyncSession,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Finds all classifications that have at least one failed sync step and
    retries them by re-building the payload from the current DB state.

    The per-step idempotency guards in sync_classification_to_sharepoint
    ensure that already-succeeded steps are safely skipped on retry.

    Returns a summary dict with counts for reporting.
    """
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
            options=[selectinload(ClassificationRecord.document)]
        )
        if not cls_record:
            continue

        # Mark the old log as retrying
        log.status = "retrying"
        await db.flush()

        payload = SyncPayload(
            classification_record_id=cls_record.id,
            document_name=cls_record.document.name if cls_record.document else "Unknown",
            document_type=cls_record.document_type,
            category=cls_record.category,
            section=cls_record.section,
            retention_code=cls_record.retention_code,
            retention_rule=cls_record.retention_rule,
            retention_end_date=cls_record.retention_end_date,
            confidence_score=cls_record.confidence_score,
            team_owner=cls_record.team_owner,
            classification_status=cls_record.status,
            # document_number: reuse the existing number — never reassign on retry
            document_number=cls_record.document.document_number if cls_record.document else None,
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
    return {"attempted": attempted, "recovered": recovered, "still_failing": still_failing}

