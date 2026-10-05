import io
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from app.config import (
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
    AZURE_DOCUMENT_INTELLIGENCE_KEY,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

document_intelligence_client = DocumentIntelligenceClient(
    endpoint=AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
    credential=AzureKeyCredential(AZURE_DOCUMENT_INTELLIGENCE_KEY),
)


async def analyze_document_raw(file_bytes: bytes):
    logger.info("Calling Azure Document Intelligence 'prebuilt-layout' model...")
    poller = document_intelligence_client.begin_analyze_document(
        "prebuilt-layout",
        io.BytesIO(file_bytes),
        content_type="application/octet-stream",
    )
    result = poller.result()
    logger.info("Azure analysis completed successfully.")
    return result
