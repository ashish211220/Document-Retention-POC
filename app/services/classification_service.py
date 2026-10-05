
"""
Classification Service.

Orchestrates the full document classification pipeline:

  DocumentProfile + DocumentUnderstanding
          │
          ▼
  Azure AI Search  →  Top 5 Retention Candidates
          │
          ▼
  Azure OpenAI     →  Select Best Candidate (by ID only)
          │
          ▼
  Validation       →  Verify against Approved Taxonomy
          │
          ▼
  Confidence Score →  Combined score from search rank + AI confidence + keyword overlap
          │
          ▼
  ClassificationResult (auto_approved | review_recommended | pending_review)

CRITICAL BUSINESS RULE:
  Azure OpenAI MUST only select a candidate from the provided list.
  It must NEVER invent retention periods, labels, categories, or rules.
"""
import json
from typing import Optional, List
from app.models.document import DocumentProfile
from app.models.classification import DocumentUnderstanding, ClassificationResult, ClassificationCandidate
from app.models.retention import RetentionRecord
from app.services.metadata_service import search_by_keywords
from app.services.validation_service import validate_candidate, determine_status
from app.services.retention_schedule_service import calculate_retention_schedule
from app.azure.openai import chat_completion, parse_json_response
from app.utils.logger import get_logger

logger = get_logger(__name__)

TOP_K_CANDIDATES = 5

CLASSIFICATION_SYSTEM_PROMPT = """You are a document classification expert for an enterprise records retention system.

You will be given:
1. A document profile (name, summary, keywords, type indicators)
2. A list of approved retention taxonomy candidates (each with an ID, document type, category, section)

Your task is to select the SINGLE BEST matching candidate from the provided list.

STRICT RULES:
1. You MUST select exactly one candidate from the provided list using its exact ID.
2. You MUST NOT invent or create new categories, sections, document types, retention rules, or retention labels.
3. You MUST NOT return any candidate ID that is not in the provided list.
4. If none of the candidates are a good match, select the closest one and set confidence below 0.5.
5. Respond ONLY with valid JSON. No explanation, no markdown.

Return ONLY this JSON structure:
{
  "selected_id": "RET-X-X-XXX",
  "confidence": 0.0,
  "reason": "Brief explanation of why this candidate was selected"
}"""


def _build_candidates_text(candidates: List[RetentionRecord]) -> str:
    """Format taxonomy candidates as structured text for the AI prompt."""
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. ID: {c.id}\n"
            f"   Document Type: {c.document_type}\n"
            f"   Category: {c.category}\n"
            f"   Section: {c.section}\n"
            f"   Description: {c.description}\n"
            f"   Keywords: {', '.join(c.keywords)}\n"
            f"   Owner: {c.team_owner}"
        )
    return "\n\n".join(lines)


def _calculate_confidence(
    ai_confidence: float,
    candidate_rank: int,
    understanding: DocumentUnderstanding,
    selected_record: RetentionRecord,
) -> float:
    """
    Combine multiple signals into a final confidence score.

    Weights:
      - AI model confidence:    40%
      - Search candidate rank:  35%  (rank 1 = 1.0, rank 5 = 0.2)
      - Keyword overlap:        25%
    """
    # Search rank score: 1st candidate = 1.0, 5th = 0.2 (linear decay)
    rank_score = max(0.0, 1.0 - (candidate_rank - 1) * 0.2)

    # Keyword overlap score
    # Split keywords into individual words to allow partial phrase matches
    understanding_words = set(word.strip() for kw in understanding.keywords for word in kw.lower().split())
    record_words = set(word.strip() for kw in selected_record.keywords for word in kw.lower().split())
    doc_type_words = set(selected_record.document_type.lower().split())
    all_record_words = record_words | doc_type_words

    # Remove common stop words for better overlap calculation
    stop_words = {"and", "or", "the", "a", "of", "to", "in", "for", "on", "with"}
    understanding_words -= stop_words
    all_record_words -= stop_words

    if all_record_words and understanding_words:
        overlap = len(understanding_words & all_record_words) / max(len(all_record_words), 1)
        keyword_score = min(1.0, overlap * 2)  # scale up, cap at 1.0
    else:
        keyword_score = 0.0

    # Weighted combination
    combined = (ai_confidence * 0.40) + (rank_score * 0.35) + (keyword_score * 0.25)
    combined = round(min(1.0, max(0.0, combined)), 3)

    logger.info(
        f"Confidence scoring: ai={ai_confidence:.2f} rank={rank_score:.2f} "
        f"keywords={keyword_score:.2f} → combined={combined:.3f}"
    )
    return combined


def classify_document(
    profile: DocumentProfile,
    understanding: DocumentUnderstanding,
) -> ClassificationResult:
    """
    Run the full classification pipeline for a document.

    Args:
        profile: Normalized DocumentProfile from Document Intelligence.
        understanding: Structured understanding from Azure OpenAI.

    Returns:
        ClassificationResult with status and confidence score.
    """
    logger.info(f"Classification started for document: {profile.document_name}")

    # ── Step 1: Build semantic search query ───────────────────────────────────
    # Instead of just keywords, we build a rich semantic string for the embedding model
    semantic_parts = []
    if understanding.title:
        semantic_parts.append(f"Title: {understanding.title}")
    if understanding.summary:
        semantic_parts.append(f"Summary: {understanding.summary}")
    if understanding.department:
        semantic_parts.append(f"Department: {understanding.department}")
    if understanding.document_type_indicators:
        semantic_parts.append(f"Type: {', '.join(understanding.document_type_indicators)}")
    if understanding.keywords:
        semantic_parts.append(f"Keywords: {', '.join(understanding.keywords)}")

    semantic_query = " | ".join(semantic_parts)
    logger.info(f"Semantic search query: '{semantic_query}'")

    # ── Step 2: Retrieve Top 5 candidates from Azure AI Search ───────────────
    # Pass the full semantic string to hybrid search (wrapped in list to match signature)
    candidate_records: List[RetentionRecord] = search_by_keywords([semantic_query], top_k=TOP_K_CANDIDATES)

    if not candidate_records:
        logger.warning("No candidates found from search — returning pending_review.")
        return ClassificationResult(
            document_id=profile.document_id,
            classification_status="pending_review",
            review_required=True,
            review_reasons=["No matching retention taxonomy candidates found. Manual classification required."],
            reason="No matching retention taxonomy candidates found. Manual classification required.",
        )

    logger.info(f"Retrieved {len(candidate_records)} candidates from search.")

    # ── Step 3: Format candidates for OpenAI prompt ──────────────────────────
    candidates_text = _build_candidates_text(candidate_records)
    candidate_objects = [
        ClassificationCandidate(
            id=r.id,
            category=r.category,
            section=r.section,
            document_type=r.document_type,
            retention_label=r.retention_label,
            retention_rule=r.retention_rule,
            retention_code=r.retention_code,
            retention_period=r.retention_period,
            retention_period_unit=r.retention_period_unit,
            classification=r.classification,
            team_owner=r.team_owner,
            description=r.description,
            search_score=None,
        )
        for r in candidate_records
    ]

    user_prompt = f"""Document to Classify:
  Name: {profile.document_name}
  Title: {understanding.title or 'Unknown'}
  Summary: {understanding.summary or 'Not available'}
  Keywords: {', '.join(understanding.keywords)}
  Type Indicators: {', '.join(understanding.document_type_indicators)}
  Department: {understanding.department or 'Unknown'}
  Team Owner: {understanding.team_owner or 'Unknown'}

Approved Retention Taxonomy Candidates:
{candidates_text}

Select the single best matching candidate from the list above. Return only the JSON object."""

    # ── Step 4: Azure OpenAI — Select best candidate ─────────────────────────
    try:
        raw_response = chat_completion(CLASSIFICATION_SYSTEM_PROMPT, user_prompt, temperature=0.0)
        parsed = parse_json_response(raw_response)
    except Exception as e:
        logger.error(f"OpenAI classification call failed: {e}")
        return ClassificationResult(
            document_id=profile.document_id,
            classification_status="pending_review",
            review_required=True,
            review_reasons=[f"AI classification failed: {str(e)}. Manual review required."],
            reason=f"AI classification failed: {str(e)}. Manual review required.",
            candidates=candidate_objects,
        )

    selected_id = parsed.get("selected_id", "").strip()
    ai_confidence = float(parsed.get("confidence", 0.5))
    ai_reason = parsed.get("reason", "")

    logger.info(f"AI selected candidate: '{selected_id}' with confidence {ai_confidence:.2f}")
    logger.info(f"AI reason: {ai_reason}")

    # ── Step 5: Validate AI selection against approved taxonomy ───────────────
    is_valid, taxonomy_record, validation_reason = validate_candidate(selected_id, candidate_objects)

    if not is_valid:
        return ClassificationResult(
            document_id=profile.document_id,
            classification_status="pending_review",
            review_required=True,
            review_reasons=[validation_reason],
            reason=validation_reason,
            candidates=candidate_objects,
        )

    # ── Step 6: Calculate confidence score ────────────────────────────────────
    # Find the rank of the selected candidate in the search results
    selected_rank = next(
        (i + 1 for i, r in enumerate(candidate_records) if r.id == selected_id),
        TOP_K_CANDIDATES  # default to last rank if not found
    )
    confidence_score = _calculate_confidence(ai_confidence, selected_rank, understanding, taxonomy_record)

    # ── Step 7: Determine status from confidence thresholds ───────────────────
    status = determine_status(confidence_score)

    reason = (
        f"AI selected '{taxonomy_record.document_type}' (ID: {taxonomy_record.id}) "
        f"with combined confidence {confidence_score:.0%}. "
        f"AI rationale: {ai_reason}"
    )

    # ── Step 8: Calculate Retention Schedule & Lifecycle Status ───────────────
    schedule = calculate_retention_schedule(
        retention_rule=taxonomy_record.retention_rule,
        document_date=understanding.document_date if understanding else None,
    )

    logger.info(
        f"Retention schedule: start={schedule.retention_start_date} end={schedule.retention_end_date} "
        f"period='{schedule.retention_period}' status='{schedule.lifecycle_status}' "
        f"review_required={schedule.review_required}"
    )

    # ── Step 9: Aggregate all review reasons ─────────────────────────────────
    review_reasons = []

    # Reason 1: Low confidence classification
    if status == "pending_review":
        review_reasons.append(f"Low AI confidence score ({confidence_score:.0%}) — below review threshold.")
    elif status == "review_recommended":
        review_reasons.append(f"Moderate AI confidence score ({confidence_score:.0%}) — review recommended.")

    # Reason 2: Retention rule requires human input
    if schedule.review_required and schedule.review_reason:
        review_reasons.append(schedule.review_reason)

    # Reason 3: Missing document date (fallback used)
    if not (understanding and understanding.document_date):
        review_reasons.append("Document date not found — today's date was used as retention start date. Verify with actual document date.")

    # Overall review_required is True if ANY signal requires it
    final_review_required = schedule.review_required or status in ("pending_review", "review_recommended") or len(review_reasons) > 0

    return ClassificationResult(
        document_id=profile.document_id,
        selected_candidate_id=selected_id,
        category=taxonomy_record.category,
        section=taxonomy_record.section,
        document_type=taxonomy_record.document_type,
        retention_label=taxonomy_record.retention_label,
        retention_rule=taxonomy_record.retention_rule,
        retention_code=taxonomy_record.retention_code,
        team_owner=taxonomy_record.team_owner,
        classification_type="Auto",
        confidence_score=confidence_score,
        classification_status=status,
        reason=reason,
        candidates=candidate_objects,
        retention_start_date=schedule.retention_start_date,
        retention_end_date=schedule.retention_end_date,
        retention_period=taxonomy_record.retention_period,
        retention_period_unit=taxonomy_record.retention_period_unit,
        lifecycle_status=schedule.lifecycle_status,
        review_required=final_review_required,
        review_reasons=review_reasons,
        next_action=schedule.next_action,
        trigger_condition=schedule.trigger_condition,
    )
