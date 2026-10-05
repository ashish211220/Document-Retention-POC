"""
SharePoint API Router.

Authentication model (app-only / client credentials):
  - NO device code flow, NO user sign-in, NO /auth/device-code endpoint.
  - graph_auth_service.get_access_token() is called per-request and handles
    MSAL in-memory caching + silent token refresh automatically.
  - Requires SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID
    to be set in the environment.

Routes:
  GET  /api/sharepoint/documents          — List files in POC_Source_Documents
  POST /api/sharepoint/sync/{item_id}     — Classify + sync a single document
  POST /api/sharepoint/sync/bulk          — Queue all documents for background sync
  POST /api/sharepoint/sync/retry         — Retry any failed sync steps
"""
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.graph_auth_service import graph_auth_service
from app.services.sharepoint_service import sharepoint_service
from app.services.document_intelligence_service import analyze_and_normalize_document
from app.services.document_understanding_service import analyze_document
from app.services.classification_service import classify_document
from app.db.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_token() -> str:
    """
    Acquires a Graph API bearer token using the client credentials flow.
    MSAL handles caching — this is fast on cache hits (no network call).
    Raises HTTP 503 if the auth service is not configured.
    """
    if not graph_auth_service:
        raise HTTPException(
            status_code=503,
            detail=(
                "SharePoint auth is not configured. "
                "Set SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, and SHAREPOINT_TENANT_ID."
            ),
        )
    try:
        return graph_auth_service.get_access_token()
    except Exception as exc:
        logger.error(f"Token acquisition failed: {exc}")
        raise HTTPException(status_code=503, detail=f"Could not acquire Graph API token: {exc}")


# ── Response Models ───────────────────────────────────────────────────────────

class SyncResponse(BaseModel):
    item_id: str
    filename: str
    classification_status: str
    confidence_score: Optional[float] = None
    retention_code: Optional[str] = None
    retention_rule: Optional[str] = None
    sharepoint_updated: bool


# ── List Documents ────────────────────────────────────────────────────────────

@router.get("/documents", summary="List files in POC_Source_Documents library")
async def list_documents():
    """
    Returns the files currently in the configured SharePoint document library.
    Token is acquired automatically via client credentials — no login needed.
    """
    token = _get_token()
    try:
        docs = await sharepoint_service.get_documents(token)
        return {"total": len(docs), "documents": docs}
    except Exception as exc:
        logger.error(f"Failed to list SharePoint documents: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Single Document Sync ──────────────────────────────────────────────────────

async def _process_single_document(token: str, item_id: str, filename: str) -> SyncResponse:
    """
    Full pipeline for one document:
      1. Download from SharePoint
      2. OCR via Azure Document Intelligence
      3. Document Understanding via Azure OpenAI
      4. Classification via hybrid search + LLM
      5. Sync results back to SharePoint (via sharepoint_sync module)
    """
    # 1. Download
    logger.info(f"[{filename}] Downloading from SharePoint (item_id={item_id})...")
    file_content = await sharepoint_service.download_document(token, item_id)

    # 2. OCR
    logger.info(f"[{filename}] Extracting text via Azure Document Intelligence...")
    document_profile = await analyze_and_normalize_document(file_content, filename, source="SharePoint")

    # 3. Understanding
    logger.info(f"[{filename}] Running document understanding via Azure OpenAI...")
    understanding = analyze_document(document_profile)
    if not understanding:
        raise Exception("Document understanding returned no result.")

    # 4. Classify
    logger.info(f"[{filename}] Classifying...")
    classification = classify_document(document_profile, understanding)
    logger.info(
        f"[{filename}] Classification complete — status={classification.classification_status} "
        f"confidence={classification.confidence_score:.3f} "
        f"retention_code={classification.retention_code}"
    )

    # 5. Sync back to SharePoint
    # NOTE: full sync (Purview label + metadata + list row + audit log) is handled
    # by app/sync/sharepoint_sync.py. The direct call below is a lightweight fallback
    # for POC / ad-hoc single-document calls that don't go through the DB pipeline.
    sharepoint_updated = False
    if classification.retention_code:
        try:
            await sharepoint_service.update_metadata_and_label(
                token=token,
                item_id=item_id,
                metadata={},
                retention_code=classification.retention_code,
            )
            sharepoint_updated = True
            logger.info(f"[{filename}] SharePoint retention label updated.")
        except Exception as exc:
            logger.warning(f"[{filename}] SharePoint update failed (non-fatal for POC): {exc}")
    else:
        logger.warning(f"[{filename}] No retention_code — skipping SharePoint update.")

    return SyncResponse(
        item_id=item_id,
        filename=filename,
        classification_status=classification.classification_status,
        confidence_score=classification.confidence_score,
        retention_code=classification.retention_code,
        retention_rule=classification.retention_rule,
        sharepoint_updated=sharepoint_updated,
    )


@router.post("/sync/{item_id}", response_model=SyncResponse, summary="Classify and sync a single document")
async def sync_document(item_id: str, filename: str):
    """
    Downloads, classifies, and syncs a single SharePoint document by its item ID.
    Token is acquired automatically — no auth step needed.
    """
    token = _get_token()
    try:
        return await _process_single_document(token, item_id, filename)
    except Exception as exc:
        logger.error(f"sync_document failed for {item_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Bulk Sync ─────────────────────────────────────────────────────────────────

async def _bulk_process_background(token: str, documents: List[Dict[str, Any]]):
    logger.info(f"Bulk sync started — {len(documents)} documents queued.")
    success_count = failure_count = 0

    for doc in documents:
        item_id = doc.get("id")
        drive_item = doc.get("driveItem", {})
        filename = drive_item.get("name") or doc.get("name") or f"Document_{item_id}"

        if not item_id:
            logger.warning("Skipping document with missing item ID.")
            continue

        try:
            logger.info(f"--- Bulk: {filename} ---")
            # Re-acquire token per-document to handle expiry in long-running batches
            fresh_token = graph_auth_service.get_access_token() if graph_auth_service else token
            await _process_single_document(fresh_token, item_id, filename)
            success_count += 1
        except Exception as exc:
            logger.error(f"Bulk failed for {filename}: {exc}")
            failure_count += 1

    logger.info(f"Bulk sync complete — success={success_count}, failed={failure_count}")


@router.post("/sync/bulk", summary="Queue all documents in the library for background sync")
async def sync_bulk_documents(background_tasks: BackgroundTasks):
    """
    Fetches all files from POC_Source_Documents and queues them for background
    classification + sync. Token is acquired automatically.
    """
    token = _get_token()
    try:
        docs = await sharepoint_service.get_documents(token)
        if not docs:
            return {"message": "No documents found in SharePoint library.", "queued_count": 0}

        background_tasks.add_task(_bulk_process_background, token, docs)
        return {
            "message": f"Queued {len(docs)} documents for background processing.",
            "queued_count": len(docs),
            "status": "processing_in_background",
        }
    except Exception as exc:
        logger.error(f"Bulk sync init failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Retry Failed Syncs ────────────────────────────────────────────────────────

@router.post("/sync/retry", summary="Retry all failed SharePoint sync steps")
async def retry_failed(db: AsyncSession = Depends(get_db)):
    """
    Finds all classifications with failed sync steps (from sharepoint_sync_logs)
    and retries them. Idempotency guards ensure already-succeeded steps are skipped.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    token = _get_token()
    try:
        from app.sync.sharepoint_sync import retry_failed_syncs
        summary = await retry_failed_syncs(token=token, db=db)
        return {"retry_summary": summary}
    except Exception as exc:
        logger.error(f"Retry sync failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

