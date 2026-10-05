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


def validate_candidate(candidate_id: str, candidates: list[ClassificationCandidate]) -> Tuple[bool, Optional[RetentionRecord], str]:
    """
    Validate that the AI-selected candidate ID:
      1. Exists in the list of candidates that were actually provided to the AI.
      2. Exists in the approved retention taxonomy knowledge base.

    Args:
        candidate_id: The ID the AI selected (e.g. "RET-2-1-013").
        candidates: The list of candidates that were presented to the AI.

    Returns:
        Tuple of (is_valid: bool, taxonomy_record: RetentionRecord | None, reason: str)
    """
    # Step 1: Verify the ID is from the provided candidate list (AI cannot invent IDs)
    provided_ids = {c.id for c in candidates}
    if candidate_id not in provided_ids:
        reason = (
            f"AI selected candidate '{candidate_id}' which was not in the provided candidate list. "
            f"Provided IDs: {sorted(provided_ids)}. Classification flagged for human review."
        )
        logger.warning(reason)
        return False, None, reason

    # Step 2: Verify the ID exists in the approved taxonomy (ground truth)
    taxonomy_record = get_record_by_id(candidate_id)
    if taxonomy_record is None:
        reason = (
            f"Candidate ID '{candidate_id}' not found in approved retention taxonomy. "
            "Classification flagged for human review."
        )
        logger.warning(reason)
        return False, None, reason

    logger.info(f"Candidate '{candidate_id}' validated successfully: '{taxonomy_record.document_type}'")
    return True, taxonomy_record, f"Valid taxonomy record found: {taxonomy_record.document_type}"


def determine_status(confidence_score: float) -> str:
    """
    Map a confidence score (0.0-1.0 scale) to a human-readable classification status.

    SCALE NOTE: AUTO_TAG_CONFIDENCE_THRESHOLD is expressed on a 0-100 scale.
    The normalization `confidence_score * 100 >= threshold` is done here and
    ONLY here. Do not compare raw scores against the threshold anywhere else.

    Thresholds:
      >= AUTO_TAG_CONFIDENCE_THRESHOLD / 100  → auto_approved (Auto-Tagged in SP)
      >= MEDIUM_CONFIDENCE_THRESHOLD          → review_recommended
      Below medium                             → pending_review (Review Pending in SP)

    Returns one of: 'auto_approved' | 'review_recommended' | 'pending_review'
    """
    from app.config import AUTO_TAG_CONFIDENCE_THRESHOLD, MEDIUM_CONFIDENCE_THRESHOLD
    # Normalize: confidence_score is 0-1; threshold is expressed as 0-100.
    # >= 60 (threshold) means >= 0.60 in score terms. Boundary is inclusive (>=).
    if confidence_score * 100 >= AUTO_TAG_CONFIDENCE_THRESHOLD:
        return "auto_approved"
    if confidence_score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "review_recommended"
    return "pending_review"


def determine_document_tagged_status(classification_status: str) -> str:
    """
    Map an internal classification_status to the DocumentTagged SP choice value.

    Mapping:
      'auto_approved'        → DocumentTaggedStatus.AUTO_TAGGED   ('Auto-Tagged')
      'review_recommended'   → DocumentTaggedStatus.REVIEW_PENDING ('Review Pending')
      'pending_review'       → DocumentTaggedStatus.REVIEW_PENDING ('Review Pending')
      anything else          → DocumentTaggedStatus.REVIEW_PENDING (safe default)

    Args:
        classification_status: The internal status string from classify_document().

    Returns:
        An exact SharePoint choice string from DocumentTaggedStatus.
    """
    from app.models.sharepoint_enums import DocumentTaggedStatus
    if classification_status == "auto_approved":
        return DocumentTaggedStatus.AUTO_TAGGED.value
    return DocumentTaggedStatus.REVIEW_PENDING.value

