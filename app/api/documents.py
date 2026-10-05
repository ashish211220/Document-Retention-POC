"""
Documents API.

Routes:
  POST /api/documents/upload                        - Upload and process a PDF document
  GET  /api/documents/stats                         - Dashboard aggregate counts
  GET  /api/documents/review                        - Paginated queue of documents requiring review
  GET  /api/documents/{id}/review                   - Full review details for a document
  POST /api/documents/{id}/review/retain            - Submit a "retain" decision
  POST /api/documents/{id}/review/approve-deletion  - Submit a deletion approval decision
  POST /api/documents/{id}/review/reclassify        - Request reclassification
  POST /api/documents/{id}/legal-hold               - Apply or remove a legal hold
  GET  /api/documents/{id}/audit                    - Full audit history for a document
"""
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from app.utils.file_validator import validate_pdf_upload
from app.services.document_intelligence_service import analyze_and_normalize_document
from app.services.document_understanding_service import analyze_document
from app.services.classification_service import classify_document
from app.utils.logger import get_logger
from app.db.database import get_db
from app.models.db_models import DocumentRecord, ClassificationRecord, ProcessingHistory, ReviewRecord, AuditLog

router = APIRouter()
logger = get_logger(__name__)


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ReviewDecisionRequest(BaseModel):
    reviewer_id: str
    comments: Optional[str] = None


class ReclassifyRequest(BaseModel):
    reviewer_id: str
    reason: str
    suggested_category: Optional[str] = None
    suggested_document_type: Optional[str] = None
    comments: Optional[str] = None


class LegalHoldRequest(BaseModel):
    reviewer_id: str
    apply: bool          # True = apply hold, False = remove hold
    comments: Optional[str] = None


class ReviewQueueItem(BaseModel):
    document_id: str
    document_name: str
    document_type: Optional[str] = None
    category: Optional[str] = None
    retention_label: Optional[str] = None
    retention_rule: Optional[str] = None        # Actual rule code e.g. FE+4, AL+2
    retention_end_date: Optional[str] = None
    confidence_score: Optional[float] = None
    review_reasons: Optional[list] = None
    review_status: Optional[str] = None
    lifecycle_status: Optional[str] = None
    team_owner: Optional[str] = None
    legal_hold: Optional[bool] = None
    created_at: Optional[str] = None


class ReviewDetailResponse(BaseModel):
    document_id: str
    document_name: str
    file_type: Optional[str] = None
    page_count: Optional[int] = None
    created_at: Optional[str] = None
    # Classification
    classification_status: Optional[str] = None
    confidence_score: Optional[float] = None
    reason: Optional[str] = None
    selected_candidate_id: Optional[str] = None
    category: Optional[str] = None
    section: Optional[str] = None
    document_type: Optional[str] = None
    retention_label: Optional[str] = None
    retention_rule: Optional[str] = None
    team_owner: Optional[str] = None
    # Retention
    retention_start_date: Optional[str] = None
    retention_end_date: Optional[str] = None
    retention_period: Optional[str] = None
    lifecycle_status: Optional[str] = None
    review_required: Optional[bool] = None
    review_reasons: Optional[list] = None
    review_status: Optional[str] = None
    legal_hold: Optional[bool] = None
    next_action: Optional[str] = None
    trigger_condition: Optional[str] = None
    # Review history
    review_records: Optional[list] = None
    audit_history: Optional[list] = None


# ── Upload Endpoint ───────────────────────────────────────────────────────────

@router.post("/upload", summary="Upload and process a PDF document")
async def upload_document(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    logger.info(f"Document upload started: {file.filename}")

    # ── Step 1: Validate file ─────────────────────────────────────────────────
    try:
        validate_pdf_upload(file)
        logger.info(f"File validation completed for: {file.filename}")
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File read/validation error: {e}")
        raise HTTPException(status_code=400, detail="Invalid file upload.")

    # ── Step 2: Azure Document Intelligence — OCR + Extraction ───────────────
    try:
        logger.info(f"Sending to Azure Document Intelligence: {file.filename}")
        document_profile = await analyze_and_normalize_document(file_bytes, file.filename)
        logger.info(f"Document profile created: {document_profile.document_id}")
    except Exception as e:
        logger.error(f"Azure Document Intelligence failed: {e}")
        raise HTTPException(
            status_code=502,
            detail={
                "success": False,
                "error": {
                    "code": "DOCUMENT_EXTRACTION_FAILED",
                    "message": "Unable to extract document content from Azure Document Intelligence."
                }
            }
        )

    # ── Step 2.5: Check OCR Confidence ───────────────────────────────────────
    OCR_THRESHOLD = 0.60
    skip_ai_analysis = False
    if document_profile.ocr_confidence is not None and document_profile.ocr_confidence < OCR_THRESHOLD:
        logger.warning(f"OCR Confidence ({document_profile.ocr_confidence:.2f}) is below threshold ({OCR_THRESHOLD}). Skipping OpenAI.")
        skip_ai_analysis = True

    # ── Step 3: Azure OpenAI — Document Understanding ────────────────────────
    understanding = None
    if not skip_ai_analysis:
        try:
            logger.info(f"Running document understanding: {file.filename}")
            understanding = analyze_document(document_profile)
            if understanding:
                logger.info(f"Understanding complete. Query: '{understanding.suggested_search_query}'")
            else:
                logger.warning("Document understanding skipped (Azure OpenAI not configured or failed).")
        except Exception as e:
            logger.error(f"Document understanding error (non-fatal): {e}")

    # ── Step 4: Classification Pipeline ──────────────────────────────────────
    classification = None
    if skip_ai_analysis:
        from app.models.classification import ClassificationResult
        classification = ClassificationResult(
            document_id=document_profile.document_id,
            classification_status="pending_review",
            review_required=True,
            review_reasons=[f"OCR scan quality too low (Confidence: {document_profile.ocr_confidence:.2f}). Please upload a clearer document."],
            reason=f"OCR scan quality too low (Confidence: {document_profile.ocr_confidence:.2f}). Please upload a clearer document.",
        )
    elif understanding:
        try:
            logger.info(f"Starting classification pipeline: {file.filename}")
            classification = classify_document(document_profile, understanding)
            logger.info(
                f"Classification complete: status={classification.classification_status} "
                f"confidence={classification.confidence_score:.3f} "
                f"review_required={classification.review_required}"
            )
        except Exception as e:
            logger.error(f"Classification error (non-fatal): {e}")
    else:
        logger.warning("Classification skipped — document understanding was not available.")
        
    # ── Step 5: Save to Database ─────────────────────────────────────────────
    if db:
        try:
            # Create DocumentRecord
            doc_record = DocumentRecord(
                document_id=document_profile.document_id,
                name=document_profile.document_name,
                source=document_profile.source,
                file_type=document_profile.file_type,
                page_count=document_profile.metadata.page_count
            )
            db.add(doc_record)
            await db.flush()
            
            # Create ProcessingHistory
            history = ProcessingHistory(
                document_id=doc_record.id,
                stage="upload",
                status="completed",
                message="Document processed and classification attempted."
            )
            db.add(history)
            
            # Create ClassificationRecord with enriched fields
            if classification:
                selected_id = None
                if classification.candidates and classification.classification_status != "pending_review":
                    selected_id = classification.candidates[0].id
                class_record = ClassificationRecord(
                    document_id=doc_record.id,
                    status=classification.classification_status,
                    confidence_score=classification.confidence_score,
                    selected_candidate_id=selected_id,
                    reason=classification.reason,
                    # Taxonomy enrichment
                    category=classification.category,
                    section=classification.section,
                    document_type=classification.document_type,
                    retention_label=classification.retention_label,
                    retention_rule=classification.retention_rule,    # persist actual rule code
                    retention_code=classification.retention_code,
                    team_owner=classification.team_owner,
                    # Retention schedule
                    retention_start_date=classification.retention_start_date,
                    retention_end_date=classification.retention_end_date,
                    retention_period=classification.retention_period,
                    retention_period_unit=classification.retention_period_unit,
                    lifecycle_status=classification.lifecycle_status,
                    review_required=classification.review_required,
                    review_reasons=classification.review_reasons,
                    review_status="pending" if classification.review_required else "not_required",
                    legal_hold=False,
                    next_action=classification.next_action,
                    trigger_condition=classification.trigger_condition,
                )
                db.add(class_record)

            # Audit log: document uploaded
            audit = AuditLog(
                document_id=doc_record.id,
                event_type="DOCUMENT_UPLOADED",
                event_data={
                    "filename": file.filename,
                    "classification_status": classification.classification_status if classification else None,
                    "review_required": classification.review_required if classification else None,
                }
            )
            db.add(audit)
                
            await db.commit()
            logger.info("Successfully saved processing results to the database.")
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to save records to database: {e}")

    # ── Response ─────────────────────────────────────────────────────────────
    return {
        "success": True,
        "message": "Document processed successfully",
        "document": document_profile.model_dump(),
        "understanding": understanding.model_dump() if understanding else None,
        "classification": classification.model_dump() if classification else None,
    }


# ── Dashboard Stats ───────────────────────────────────────────────────────────

@router.get("/stats", summary="Get aggregate dashboard counts")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Returns aggregate counts for the review dashboard KPI strip."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    total_docs_result = await db.execute(select(func.count()).select_from(DocumentRecord))
    total_docs = total_docs_result.scalar() or 0

    pending_result = await db.execute(
        select(func.count()).select_from(ClassificationRecord).where(
            and_(ClassificationRecord.review_required == True,
                 ClassificationRecord.review_status == "pending")
        )
    )
    pending_review = pending_result.scalar() or 0

    deletion_approved_result = await db.execute(
        select(func.count()).select_from(ClassificationRecord).where(
            ClassificationRecord.review_status == "approved_deletion")
    )
    deletion_approved = deletion_approved_result.scalar() or 0

    retained_result = await db.execute(
        select(func.count()).select_from(ClassificationRecord).where(
            ClassificationRecord.review_status == "retained")
    )
    retained = retained_result.scalar() or 0

    legal_hold_result = await db.execute(
        select(func.count()).select_from(ClassificationRecord).where(
            ClassificationRecord.legal_hold == True)
    )
    legal_holds = legal_hold_result.scalar() or 0

    permanent_result = await db.execute(
        select(func.count()).select_from(ClassificationRecord).where(
            ClassificationRecord.lifecycle_status == "Permanent Retention")
    )
    permanent_records = permanent_result.scalar() or 0

    return {
        "total_documents": total_docs,
        "pending_review": pending_review,
        "deletion_approved": deletion_approved,
        "retained": retained,
        "legal_holds": legal_holds,
        "permanent_records": permanent_records,
    }


# ── All Documents (SharePoint Simulator) ───────────────────────────────────────

@router.get("/all", summary="Get all documents for SharePoint Simulator")
async def get_all_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    stmt = (
        select(DocumentRecord, ClassificationRecord)
        .outerjoin(ClassificationRecord, DocumentRecord.id == ClassificationRecord.document_id)
        .order_by(DocumentRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    records = result.all()

    items = []
    for doc, cls in records:
        items.append({
            "document_id": doc.document_id,
            "document_name": doc.name,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "classification_status": cls.status if cls else "processing",
            "retention_rule": cls.retention_rule if cls else None,
            "confidence_score": cls.confidence_score if cls else None,
        })

    return {
        "page": page,
        "page_size": page_size,
        "total": len(items),
        "items": items
    }

# ── Review Queue ──────────────────────────────────────────────────────────────

@router.get("/review", summary="Get documents requiring human review")
async def get_review_queue(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    review_status: Optional[str] = Query(default=None, description="Filter by review_status (pending, retained, approved_deletion, reclassified, legal_hold)"),
    team_owner: Optional[str] = Query(default=None),
    lifecycle_status: Optional[str] = Query(default=None),
    retention_rule: Optional[str] = Query(default=None, description="Filter by retention rule code e.g. FE+4, AL+2, Permanent"),
    min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0, description="Minimum confidence score"),
    max_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0, description="Maximum confidence score"),
    db: AsyncSession = Depends(get_db)
):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    # Build filter conditions — only show review_required documents
    conditions = [ClassificationRecord.review_required == True]
    if review_status:
        conditions.append(ClassificationRecord.review_status == review_status)
    if team_owner:
        conditions.append(ClassificationRecord.team_owner.ilike(f"%{team_owner}%"))
    if lifecycle_status:
        conditions.append(ClassificationRecord.lifecycle_status.ilike(f"%{lifecycle_status}%"))
    if retention_rule:
        conditions.append(ClassificationRecord.retention_rule.ilike(f"%{retention_rule}%"))
    if min_confidence is not None:
        conditions.append(ClassificationRecord.confidence_score >= min_confidence)
    if max_confidence is not None:
        conditions.append(ClassificationRecord.confidence_score <= max_confidence)

    # Get accurate total count
    count_stmt = (
        select(func.count())
        .select_from(DocumentRecord)
        .join(ClassificationRecord, DocumentRecord.id == ClassificationRecord.document_id)
        .where(and_(*conditions))
    )
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0

    # Paginated items
    stmt = (
        select(DocumentRecord, ClassificationRecord)
        .join(ClassificationRecord, DocumentRecord.id == ClassificationRecord.document_id)
        .where(and_(*conditions))
        .order_by(ClassificationRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for doc, cls in rows:
        items.append(ReviewQueueItem(
            document_id=doc.document_id,
            document_name=doc.name,
            document_type=cls.document_type,
            category=cls.category,
            retention_label=cls.retention_label,
            retention_rule=cls.retention_rule,           # actual rule code, not taxonomy ID
            retention_end_date=cls.retention_end_date,
            confidence_score=cls.confidence_score,
            review_reasons=cls.review_reasons,
            review_status=cls.review_status,
            lifecycle_status=cls.lifecycle_status,
            team_owner=cls.team_owner,
            legal_hold=cls.legal_hold,
            created_at=doc.created_at.isoformat() if doc.created_at else None,
        ).model_dump())

    return {
        "page": page,
        "page_size": page_size,
        "total": total_count,          # true DB total, not page size
        "total_pages": max(1, -(-total_count // page_size)),
        "items": items,
    }


# ── Review Detail ─────────────────────────────────────────────────────────────

@router.get("/{document_id}/review", summary="Get full review details for a document")
async def get_review_detail(document_id: str, db: AsyncSession = Depends(get_db)):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    # Fetch document
    doc_result = await db.execute(
        select(DocumentRecord).where(DocumentRecord.document_id == document_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")

    # Fetch classification
    cls_result = await db.execute(
        select(ClassificationRecord).where(ClassificationRecord.document_id == doc.id)
    )
    cls = cls_result.scalar_one_or_none()

    # Fetch review records
    review_result = await db.execute(
        select(ReviewRecord).where(ReviewRecord.document_id == doc.id).order_by(ReviewRecord.reviewed_at.desc())
    )
    reviews = review_result.scalars().all()

    # Fetch audit logs
    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.created_at.desc())
    )
    audits = audit_result.scalars().all()

    return ReviewDetailResponse(
        document_id=doc.document_id,
        document_name=doc.name,
        file_type=doc.file_type,
        page_count=doc.page_count,
        created_at=doc.created_at.isoformat() if doc.created_at else None,
        classification_status=cls.status if cls else None,
        confidence_score=cls.confidence_score if cls else None,
        reason=cls.reason if cls else None,
        selected_candidate_id=cls.selected_candidate_id if cls else None,
        category=cls.category if cls else None,
        section=cls.section if cls else None,
        document_type=cls.document_type if cls else None,
        retention_label=cls.retention_label if cls else None,
        retention_rule=cls.retention_rule if cls else None,
        team_owner=cls.team_owner if cls else None,
        retention_start_date=cls.retention_start_date if cls else None,
        retention_end_date=cls.retention_end_date if cls else None,
        retention_period=cls.retention_period if cls else None,
        lifecycle_status=cls.lifecycle_status if cls else None,
        review_required=cls.review_required if cls else None,
        review_reasons=cls.review_reasons if cls else None,
        review_status=cls.review_status if cls else None,
        legal_hold=cls.legal_hold if cls else None,
        next_action=cls.next_action if cls else None,
        trigger_condition=cls.trigger_condition if cls else None,
        review_records=[
            {
                "id": r.id,
                "reviewer_id": r.reviewer_id,
                "decision": r.decision,
                "comments": r.comments,
                "previous_status": r.previous_status,
                "new_status": r.new_status,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            }
            for r in reviews
        ],
        audit_history=[
            {
                "id": a.id,
                "event_type": a.event_type,
                "event_data": a.event_data,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in audits
        ],
    ).model_dump()


# ── Retain Decision ─────────────────────────────────────────────────────────

@router.post("/{document_id}/review/retain", summary="Submit a retain decision for a document")
async def retain_document(document_id: str, body: ReviewDecisionRequest, db: AsyncSession = Depends(get_db)):
    # NOTE: legal hold check happens in _get_doc_and_cls helper
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    if not body.reviewer_id or not body.reviewer_id.strip():
        raise HTTPException(status_code=400, detail="reviewer_id is required.")

    doc, cls = await _get_doc_and_cls(document_id, db)

    # Block actions while a legal hold is active
    if cls.legal_hold:
        raise HTTPException(
            status_code=409,
            detail="A legal hold is active on this document. Remove the hold before submitting a retain decision."
        )

    previous_status = cls.review_status

    # Update classification review status
    cls.review_status = "retained"
    cls.lifecycle_status = "Active"

    # Create review record
    review = ReviewRecord(
        document_id=doc.id,
        reviewer_id=body.reviewer_id,
        decision="retain",
        comments=body.comments,
        previous_status=previous_status,
        new_status="retained",
    )
    db.add(review)

    # Audit log
    audit = AuditLog(
        document_id=doc.id,
        event_type="REVIEW_RETAIN",
        event_data={
            "reviewer_id": body.reviewer_id,
            "comments": body.comments,
            "previous_status": previous_status,
            "new_status": "retained",
        }
    )
    db.add(audit)

    await db.commit()
    logger.info(f"Document '{document_id}' retained by reviewer '{body.reviewer_id}'.")

    return {
        "success": True,
        "document_id": document_id,
        "decision": "retain",
        "new_status": "retained",
        "message": f"Document retained. Reviewed by '{body.reviewer_id}'.",
    }


# ── Approve Deletion ──────────────────────────────────────────────────────────

@router.post("/{document_id}/review/approve-deletion", summary="Approve deletion of a document (does NOT delete — requires separate authorized action)")
async def approve_deletion(document_id: str, body: ReviewDecisionRequest, db: AsyncSession = Depends(get_db)):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    if not body.reviewer_id or not body.reviewer_id.strip():
        raise HTTPException(status_code=400, detail="reviewer_id is required.")

    if not body.comments or not body.comments.strip():
        raise HTTPException(status_code=400, detail="comments are required when approving deletion.")

    doc, cls = await _get_doc_and_cls(document_id, db)

    # Block deletion approval when a legal hold is active
    if cls.legal_hold:
        raise HTTPException(
            status_code=409,
            detail="A legal hold is active on this document. Deletion cannot be approved while a legal hold is in effect."
        )

    # Prevent duplicate deletion approval
    if cls.review_status == "approved_deletion":
        raise HTTPException(
            status_code=409,
            detail="Deletion has already been approved for this document. No duplicate approval allowed."
        )

    previous_status = cls.review_status

    # Update classification review status
    cls.review_status = "approved_deletion"
    cls.lifecycle_status = "Deletion Approved"

    # Create review record
    review = ReviewRecord(
        document_id=doc.id,
        reviewer_id=body.reviewer_id,
        decision="approve_deletion",
        comments=body.comments,
        previous_status=previous_status,
        new_status="approved_deletion",
    )
    db.add(review)

    # Audit log
    audit = AuditLog(
        document_id=doc.id,
        event_type="REVIEW_APPROVE_DELETION",
        event_data={
            "reviewer_id": body.reviewer_id,
            "comments": body.comments,
            "previous_status": previous_status,
            "new_status": "approved_deletion",
            "WARNING": "This approval does NOT automatically delete the document. A separate authorized deletion process is required."
        }
    )
    db.add(audit)

    await db.commit()
    logger.info(f"Deletion approved for document '{document_id}' by reviewer '{body.reviewer_id}'.")

    return {
        "success": True,
        "document_id": document_id,
        "decision": "approve_deletion",
        "new_status": "approved_deletion",
        "message": (
            f"Deletion approved by '{body.reviewer_id}'. "
            "NOTE: This does NOT automatically delete the document. "
            "A separate authorized deletion process is required."
        ),
    }


# ── Reclassify Request ───────────────────────────────────────────────────────

@router.post("/{document_id}/review/reclassify", summary="Request reclassification of a document")
async def reclassify_document(document_id: str, body: ReclassifyRequest, db: AsyncSession = Depends(get_db)):
    """
    Flag a document for reclassification. Does NOT re-run the AI pipeline —
    records the request so a compliance officer can assign a corrected taxonomy entry.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not body.reviewer_id or not body.reviewer_id.strip():
        raise HTTPException(status_code=400, detail="reviewer_id is required.")
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="reason is required for reclassification.")

    doc, cls = await _get_doc_and_cls(document_id, db)

    if cls.review_status == "approved_deletion":
        raise HTTPException(
            status_code=409,
            detail="Deletion has already been approved. Reclassification is not permitted."
        )

    previous_status = cls.review_status
    cls.review_status = "reclassified"
    cls.review_required = True  # keep in the review queue

    review = ReviewRecord(
        document_id=doc.id,
        reviewer_id=body.reviewer_id,
        decision="reclassify",
        comments=body.comments or body.reason,
        previous_status=previous_status,
        new_status="reclassified",
    )
    db.add(review)

    audit = AuditLog(
        document_id=doc.id,
        event_type="REVIEW_RECLASSIFY_REQUESTED",
        event_data={
            "reviewer_id": body.reviewer_id,
            "reason": body.reason,
            "suggested_category": body.suggested_category,
            "suggested_document_type": body.suggested_document_type,
            "comments": body.comments,
            "previous_status": previous_status,
            "new_status": "reclassified",
        }
    )
    db.add(audit)

    await db.commit()
    logger.info(f"Reclassification requested for document '{document_id}' by reviewer '{body.reviewer_id}'.")

    return {
        "success": True,
        "document_id": document_id,
        "decision": "reclassify",
        "new_status": "reclassified",
        "message": (
            f"Reclassification requested by '{body.reviewer_id}'. "
            "Document remains in the review queue until a compliance officer assigns a corrected classification."
        ),
    }


# ── Legal Hold ────────────────────────────────────────────────────────────────

@router.post("/{document_id}/legal-hold", summary="Apply or remove a legal hold on a document")
async def set_legal_hold(document_id: str, body: LegalHoldRequest, db: AsyncSession = Depends(get_db)):
    """
    Apply or remove a legal hold. While active: retain and approve-deletion are blocked.
    NOTE: POC-level control — production requires proper auth.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not body.reviewer_id or not body.reviewer_id.strip():
        raise HTTPException(status_code=400, detail="reviewer_id is required.")

    doc, cls = await _get_doc_and_cls(document_id, db)

    if cls.legal_hold == body.apply:
        state = "already applied" if body.apply else "not currently active"
        raise HTTPException(status_code=409, detail=f"Legal hold is {state} on this document.")

    previous_hold = cls.legal_hold
    cls.legal_hold = body.apply
    event_type = "LEGAL_HOLD_APPLIED" if body.apply else "LEGAL_HOLD_REMOVED"
    action_label = "applied" if body.apply else "removed"

    audit = AuditLog(
        document_id=doc.id,
        event_type=event_type,
        event_data={
            "reviewer_id": body.reviewer_id,
            "action": action_label,
            "comments": body.comments,
            "previous_legal_hold": previous_hold,
            "new_legal_hold": body.apply,
        }
    )
    db.add(audit)

    await db.commit()
    logger.info(f"Legal hold {action_label} for document '{document_id}' by reviewer '{body.reviewer_id}'.")

    return {
        "success": True,
        "document_id": document_id,
        "legal_hold": body.apply,
        "message": (
            f"Legal hold {action_label} by '{body.reviewer_id}'. "
            + (
                "Retain and deletion approval actions are now blocked."
                if body.apply else
                "Document can now be reviewed for retention or deletion."
            )
        ),
    }


# ── Audit History ─────────────────────────────────────────────────────────────

@router.get("/{document_id}/audit", summary="Get full audit history for a document")
async def get_audit_history(document_id: str, db: AsyncSession = Depends(get_db)):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    doc_result = await db.execute(
        select(DocumentRecord).where(DocumentRecord.document_id == document_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")

    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.created_at.asc())
    )
    audits = audit_result.scalars().all()

    return {
        "document_id": document_id,
        "document_name": doc.name,
        "audit_events": [
            {
                "id": a.id,
                "event_type": a.event_type,
                "event_data": a.event_data,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in audits
        ],
    }


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_doc_and_cls(document_id: str, db: AsyncSession):
    """Fetch document and its classification or raise 404/422."""
    doc_result = await db.execute(
        select(DocumentRecord).where(DocumentRecord.document_id == document_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")

    cls_result = await db.execute(
        select(ClassificationRecord).where(ClassificationRecord.document_id == doc.id)
    )
    cls = cls_result.scalar_one_or_none()
    if not cls:
        raise HTTPException(status_code=422, detail=f"Document '{document_id}' has no classification record.")

    return doc, cls
