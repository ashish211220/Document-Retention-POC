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
        raise HTTPException(
            status_code=503, detail=f"Could not acquire Graph API token: {exc}"
        )


class SyncResponse(BaseModel):
    item_id: str
    filename: str
    classification_status: str
    confidence_score: Optional[float] = None
    retention_code: Optional[str] = None
    retention_rule: Optional[str] = None
    sharepoint_updated: bool


@router.get("/documents", summary="List files in POC_Source_Documents library")
# API Endpoint: Fetches all files from the SharePoint document library
async def list_documents():
    token = _get_token()
    try:
        docs = await sharepoint_service.get_documents(token)
        return {"total": len(docs), "documents": docs}
    except Exception as exc:
        logger.error(f"Failed to list SharePoint documents: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# Runs the full AI extraction and classification pipeline for one document
async def _process_single_document(
    token: str, item_id: str, filename: str
) -> SyncResponse:
    logger.info(f"[{filename}] Downloading from SharePoint (item_id={item_id})...")
    file_content = await sharepoint_service.download_document(token, item_id)

    logger.info(f"[{filename}] Extracting text via Azure Document Intelligence...")
    document_profile = await analyze_and_normalize_document(
        file_content, filename, source="SharePoint"
    )

    logger.info(f"[{filename}] Running document understanding via Azure OpenAI...")
    understanding = analyze_document(document_profile)
    if not understanding:
        raise Exception("Document understanding returned no result.")

    logger.info(f"[{filename}] Classifying...")
    classification = classify_document(document_profile, understanding)
    logger.info(
        f"[{filename}] Classification complete — status={classification.classification_status} "
        f"confidence={classification.confidence_score:.3f} "
        f"retention_code={classification.retention_code}"
    )

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
            logger.warning(
                f"[{filename}] SharePoint update failed (non-fatal for POC): {exc}"
            )
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


@router.post(
    "/sync/{item_id}",
    response_model=SyncResponse,
    summary="Classify and sync a single document",
)
# API Endpoint: Manually classifies and syncs a single SharePoint document by ID
async def sync_document(item_id: str, filename: str):
    token = _get_token()
    try:
        return await _process_single_document(token, item_id, filename)
    except Exception as exc:
        logger.error(f"sync_document failed for {item_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


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
            fresh_token = (
                graph_auth_service.get_access_token() if graph_auth_service else token
            )
            await _process_single_document(fresh_token, item_id, filename)
            success_count += 1
        except Exception as exc:
            logger.error(f"Bulk failed for {filename}: {exc}")
            failure_count += 1

    logger.info(f"Bulk sync complete — success={success_count}, failed={failure_count}")


@router.post(
    "/sync/bulk", summary="Queue all documents in the library for background sync"
)
# API Endpoint: Queues all documents in the library for background processing
async def sync_bulk_documents(background_tasks: BackgroundTasks):
    token = _get_token()
    try:
        docs = await sharepoint_service.get_documents(token)
        if not docs:
            return {
                "message": "No documents found in SharePoint library.",
                "queued_count": 0,
            }

        background_tasks.add_task(_bulk_process_background, token, docs)
        return {
            "message": f"Queued {len(docs)} documents for background processing.",
            "queued_count": len(docs),
            "status": "processing_in_background",
        }
    except Exception as exc:
        logger.error(f"Bulk sync init failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sync/retry", summary="Retry all failed SharePoint sync steps")
# API Endpoint: Retries any SharePoint sync steps that failed previously
async def retry_failed(db: AsyncSession = Depends(get_db)):
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
