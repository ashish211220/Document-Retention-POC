from pydantic import BaseModel
from typing import List, Optional


class RetentionRecord(BaseModel):
    """Represents a single entry from the approved Retention Taxonomy."""

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
    expected_location: Optional[str] = None
    description: str
    keywords: List[str] = []
    policy_version: Optional[str] = None

    class Config:
        from_attributes = True


class RetentionTaxonomy(BaseModel):
    """Container for the full retention taxonomy knowledge base."""

    records: List[RetentionRecord]
    total_count: int

    @classmethod
    def from_list(cls, records: List[RetentionRecord]) -> "RetentionTaxonomy":
        return cls(records=records, total_count=len(records))
