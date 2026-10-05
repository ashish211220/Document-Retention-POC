import uuid
from app.azure.document_intelligence import analyze_document_raw
from app.models.document import (
    DocumentProfile,
    DocumentMetadata,
    DocumentStructure,
    Page,
    Paragraph,
    Table,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Extracts text from the document using Azure Document Intelligence
async def analyze_and_normalize_document(
    file_bytes: bytes, filename: str
) -> DocumentProfile:
    logger.info(f"Starting document intelligence service for {filename}")
    raw_result = await analyze_document_raw(file_bytes)
    return normalize_response(raw_result, filename)


def normalize_response(result, filename: str) -> DocumentProfile:
    document_id = str(uuid.uuid4())
    content = result.content if result.content else ""

    pages = []
    if hasattr(result, "pages") and result.pages:
        for p in result.pages:
            pages.append(
                Page(
                    page_number=p.page_number,
                    width=p.width,
                    height=p.height,
                    unit=p.unit,
                )
            )

    paragraphs = []
    if hasattr(result, "paragraphs") and result.paragraphs:
        for para in result.paragraphs:
            paragraphs.append(Paragraph(content=para.content, role=para.role))

    tables = []
    if hasattr(result, "tables") and result.tables:
        for tb in result.tables:
            cells = []
            if hasattr(tb, "cells"):
                for cell in tb.cells:
                    cells.append(
                        {
                            "row_index": cell.row_index,
                            "column_index": cell.column_index,
                            "content": cell.content,
                        }
                    )
            tables.append(
                Table(row_count=tb.row_count, column_count=tb.column_count, cells=cells)
            )

    metadata = DocumentMetadata(page_count=len(pages))
    structure = DocumentStructure(pages=pages, paragraphs=paragraphs, tables=tables)

    ocr_confidence = None
    if hasattr(result, "pages") and result.pages:
        word_confidences = []
        for p in result.pages:
            if hasattr(p, "words") and p.words:
                for w in p.words:
                    if hasattr(w, "confidence") and w.confidence is not None:
                        word_confidences.append(w.confidence)
        if word_confidences:
            ocr_confidence = sum(word_confidences) / len(word_confidences)
            logger.info(f"Calculated average OCR confidence: {ocr_confidence:.3f}")

    profile = DocumentProfile(
        document_id=document_id,
        document_name=filename,
        source="upload",
        file_type="pdf",
        content=content,
        ocr_confidence=ocr_confidence,
        metadata=metadata,
        structure=structure,
    )

    logger.info(
        f"Normalized DocumentProfile created for {filename} with ID {document_id}"
    )
    return profile
