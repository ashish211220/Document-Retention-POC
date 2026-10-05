from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class Page(BaseModel):
    page_number: int
    width: Optional[float] = None
    height: Optional[float] = None
    unit: Optional[str] = None

class Paragraph(BaseModel):
    content: str
    role: Optional[str] = None

class Table(BaseModel):
    row_count: int
    column_count: int
    cells: List[Dict[str, Any]] = []

class DocumentStructure(BaseModel):
    pages: List[Page] = []
    paragraphs: List[Paragraph] = []
    tables: List[Table] = []

class DocumentMetadata(BaseModel):
    page_count: int = 0
    created_date: Optional[str] = None
    author: Optional[str] = None

class DocumentProfile(BaseModel):
    document_id: str
    document_name: str
    source: str = "upload"
    file_type: str = "pdf"
    content: str = ""
    ocr_confidence: Optional[float] = None
    metadata: DocumentMetadata = DocumentMetadata()
    structure: DocumentStructure = DocumentStructure()
