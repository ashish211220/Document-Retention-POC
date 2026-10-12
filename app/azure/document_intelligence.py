import asyncio
import io
from typing import Optional
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from app.config import (
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
    AZURE_DOCUMENT_INTELLIGENCE_KEY,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_document_intelligence_client: Optional[DocumentIntelligenceClient] = None


def _get_document_intelligence_client() -> DocumentIntelligenceClient:
    global _document_intelligence_client
    if _document_intelligence_client is None:
        _document_intelligence_client = DocumentIntelligenceClient(
            endpoint=AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
            credential=AzureKeyCredential(AZURE_DOCUMENT_INTELLIGENCE_KEY),
        )
    return _document_intelligence_client


# Module-level singleton instance for backward compatibility
document_intelligence_client: Optional[DocumentIntelligenceClient] = None
if AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY:
    try:
        document_intelligence_client = _get_document_intelligence_client()
    except Exception as exc:
        logger.warning(f"Could not initialize document_intelligence_client at import: {exc}")


def _analyze_document_sync(file_bytes: bytes):
    client = document_intelligence_client or _get_document_intelligence_client()
    poller = client.begin_analyze_document(
        "prebuilt-layout",
        io.BytesIO(file_bytes),
        content_type="application/octet-stream",
    )
    return poller.result()


async def analyze_document_raw(file_bytes: bytes):
    logger.info("Calling Azure Document Intelligence 'prebuilt-layout' model...")
    result = await asyncio.to_thread(_analyze_document_sync, file_bytes)
    logger.info("Azure analysis completed successfully.")
    return result

