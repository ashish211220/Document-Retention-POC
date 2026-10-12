"""
Unit tests for confidence calculation in classification_service.py
"""

import pytest
from app.models.classification import DocumentUnderstanding
from app.models.retention import RetentionRecord
from app.services.classification_service import _calculate_confidence


def test_confidence_zero_keyword_overlap_cannot_clear_auto_approval():
    """
    Even with top rank (1) and high AI confidence (0.95),
    zero keyword overlap should produce at most 0.65, keeping it
    safely below the 70% (0.70) auto-approval threshold.
    """
    understanding = DocumentUnderstanding(
        keywords=["completely", "unrelated", "garbage"],
        summary="Random document",
    )
    record = RetentionRecord(
        id="RET-1-1-001",
        category="Finance",
        section="Accounting",
        document_type="Invoice",
        retention_label="7 Years",
        retention_rule="FE+7",
        retention_code="FE",
        retention_period="7",
        retention_period_unit="years",
        classification="Confidential",
        team_owner="Finance Team",
        description="Accounting invoices and receipts",
        keywords=["tax", "audit", "ledger", "payable"],
    )

    # AI confidence = 0.95, rank = 1 (top candidate)
    score = _calculate_confidence(
        ai_confidence=0.95,
        candidate_rank=1,
        understanding=understanding,
        selected_record=record,
    )

    # 0.95 * 0.35 + 1.0 * 0.30 + 0.0 * 0.35 = 0.3325 + 0.30 = 0.6325 -> 0.633
    assert score < 0.70
    assert score <= 0.65


def test_confidence_strong_match_clears_auto_approval():
    """
    When keywords overlap and AI confidence is high on rank 1,
    the score should easily clear the 0.70 auto-approval threshold.
    """
    understanding = DocumentUnderstanding(
        keywords=["tax", "audit", "invoice"],
        summary="Vendor invoice for fiscal year audit",
    )
    record = RetentionRecord(
        id="RET-1-1-001",
        category="Finance",
        section="Accounting",
        document_type="Invoice",
        retention_label="7 Years",
        retention_rule="FE+7",
        retention_code="FE",
        retention_period="7",
        retention_period_unit="years",
        classification="Confidential",
        team_owner="Finance Team",
        description="Accounting invoices and receipts",
        keywords=["tax", "audit", "invoice", "payable"],
    )

    # AI confidence = 0.90, rank = 1
    score = _calculate_confidence(
        ai_confidence=0.90,
        candidate_rank=1,
        understanding=understanding,
        selected_record=record,
    )

    # 0.90 * 0.35 + 1.0 * 0.30 + keyword_score * 0.35
    assert score >= 0.70
