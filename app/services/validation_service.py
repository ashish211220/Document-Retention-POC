"""
Validation Service.

Validates the AI's selected classification candidate against the approved
in-memory retention taxonomy (loaded from retention_taxonomy.json).

CRITICAL BUSINESS RULE:
  The AI may only select from candidates explicitly retrieved from the approved
  taxonomy. This service enforces that rule by verifying the selected candidate
  ID actually exists in the taxonomy before accepting the classification.
"""

from typing import Optional, Tuple
from app.models.classification import ClassificationCandidate
from app.models.retention import RetentionRecord
from app.services.metadata_service import get_record_by_id
from app.utils.logger import get_logger

logger = get_logger(__name__)


def validate_candidate(
    candidate_id: str, candidates: list[ClassificationCandidate]
) -> Tuple[bool, Optional[RetentionRecord], str]:
    provided_ids = {c.id for c in candidates}
    if candidate_id not in provided_ids:
        reason = (
            f"AI selected candidate '{candidate_id}' which was not in the provided candidate list. "
            f"Provided IDs: {sorted(provided_ids)}. Classification flagged for human review."
        )
        logger.warning(reason)
        return False, None, reason

    taxonomy_record = get_record_by_id(candidate_id)
    if taxonomy_record is None:
        reason = (
            f"Candidate ID '{candidate_id}' not found in approved retention taxonomy. "
            "Classification flagged for human review."
        )
        logger.warning(reason)
        return False, None, reason

    logger.info(
        f"Candidate '{candidate_id}' validated successfully: '{taxonomy_record.document_type}'"
    )
    return (
        True,
        taxonomy_record,
        f"Valid taxonomy record found: {taxonomy_record.document_type}",
    )


def determine_status(confidence_score: float) -> str:
    from app.config import AUTO_TAG_CONFIDENCE_THRESHOLD, MEDIUM_CONFIDENCE_THRESHOLD

    if confidence_score * 100 >= AUTO_TAG_CONFIDENCE_THRESHOLD:
        return "auto_approved"
    if confidence_score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "review_recommended"
    return "pending_review"


def determine_document_tagged_status(classification_status: str) -> str:
    from app.models.sharepoint_enums import DocumentTaggedStatus

    if classification_status == "auto_approved":
        return DocumentTaggedStatus.AUTO_TAGGED.value
    return DocumentTaggedStatus.REVIEW_PENDING.value
