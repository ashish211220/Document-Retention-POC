from pydantic import BaseModel
from typing import List, Optional


class DocumentUnderstanding(BaseModel):
    """
    Structured AI understanding of a document's meaning and business context.
    Produced by Azure OpenAI — used to drive candidate retrieval from Azure AI Search.

    IMPORTANT: This model must NEVER contain retention_rule, retention_label,
    or any classification data. Those come exclusively from the approved taxonomy.
    """
    title: Optional[str] = None
    summary: Optional[str] = None
    department: Optional[str] = None
    team_owner: Optional[str] = None
    document_date: Optional[str] = None
    author: Optional[str] = None
    keywords: List[str] = []
    entities: List[str] = []
    document_type_indicators: List[str] = []
    suggested_search_query: Optional[str] = None


class ClassificationCandidate(BaseModel):
    """A single candidate from the retention taxonomy returned by Azure AI Search."""
    id: str
    category: str
    section: str
    document_type: str
    retention_label: str
    retention_rule: str
    retention_code: Optional[str] = None
    retention_period: Optional[int] = None
    retention_period_unit: Optional[str] = None
    classification: Optional[str] = None
    team_owner: str
    description: str
    search_score: Optional[float] = None


class ClassificationResult(BaseModel):
    """
    Final classification result for a document.
    Populated in Phase 5 after AI selects from validated taxonomy candidates.
    """
    document_id: str
    selected_candidate_id: Optional[str] = None
    category: Optional[str] = None
    section: Optional[str] = None
    document_type: Optional[str] = None
    retention_label: Optional[str] = None
    retention_rule: Optional[str] = None
    team_owner: Optional[str] = None
    classification_type: str = "Auto"
    confidence_score: float = 0.0
    classification_status: str = "pending_review"  # pending_review | auto_approved | review_recommended
    reason: Optional[str] = None
    candidates: List[ClassificationCandidate] = []
    # Retention Lifecycle Governance
    retention_start_date: Optional[str] = None
    retention_end_date: Optional[str] = None
    retention_code: Optional[str] = None
    retention_period: Optional[int] = None
    retention_period_unit: Optional[str] = None
    lifecycle_status: str = "Active"  # Active | Eligible for Review | Permanent Retention | Indeterminate
    review_required: bool = False
    review_reasons: List[str] = []   # Aggregated list of ALL reasons requiring human review
    next_action: Optional[str] = None
    trigger_condition: Optional[str] = None
