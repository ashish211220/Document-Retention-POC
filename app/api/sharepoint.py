"""
SharePoint API Router.

Authentication model (app-only / client credentials):
  - graph_auth_service.get_access_token() is called per-request and handles
    MSAL in-memory caching + silent token refresh automatically.
  - Requires SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID
    to be set in the environment.

Routes:
  GET  /api/sharepoint/documents    — List files in POC_Source_Documents (debug/ops)
  POST /api/sharepoint/sync/retry   — Retry any failed sync steps from the DB
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.graph_auth_service import graph_auth_service
from app.services.sharepoint_service import sharepoint_service
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


@router.get("/documents", summary="List files in POC_Source_Documents library")
async def list_documents():
    # Operational/debug endpoint — lists all files visible to the scheduler
    token = _get_token()
    try:
        docs = await sharepoint_service.get_documents(token)
        return {"total": len(docs), "documents": docs}
    except Exception as exc:
        logger.error(f"Failed to list SharePoint documents: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sync/retry", summary="Retry all failed SharePoint sync steps")
async def retry_failed(db: AsyncSession = Depends(get_db)):
    # Retries any sync steps (label, metadata, list row, audit log) that failed previously
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
