from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    Boolean,
    Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base
import uuid


class DocumentRecord(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    source = Column(String, nullable=True)
    file_type = Column(String, nullable=True)
    page_count = Column(Integer, default=0)
    document_number = Column(Integer, unique=True, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    classification = relationship(
        "ClassificationRecord",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )
    history = relationship(
        "ProcessingHistory", back_populates="document", cascade="all, delete-orphan"
    )
    reviews = relationship(
        "ReviewRecord", back_populates="document", cascade="all, delete-orphan"
    )
    audit_logs = relationship(
        "AuditLog", back_populates="document", cascade="all, delete-orphan"
    )


class ClassificationRecord(Base):
    __tablename__ = "classifications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String, nullable=False)
    confidence_score = Column(Float, nullable=False)
    selected_candidate_id = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    category = Column(String, nullable=True)
    section = Column(String, nullable=True)
    document_type = Column(String, nullable=True)
    retention_label = Column(String, nullable=True)
    team_owner = Column(String, nullable=True)
    classification_type = Column(String, nullable=True)  # e.g. Confidential, Internal
    retention_rule = Column(
        String, nullable=True
    )  # Actual rule code e.g. FE+4, AL+2, Permanent
    retention_code = Column(String, nullable=True)  # e.g. FE, CE, AC
    retention_start_date = Column(String, nullable=True)
    retention_end_date = Column(String, nullable=True)
    retention_period = Column(String, nullable=True)  # Number stored as string e.g. "4"
    retention_period_unit = Column(String, nullable=True)  # e.g. "years", "months"
    lifecycle_status = Column(String, default="Active")
    review_required = Column(Boolean, default=False)
    review_reasons = Column(JSON, nullable=True)  # List of review reason strings
    review_status = Column(
        String, default="pending"
    )  # pending | retained | approved_deletion | reclassified | legal_hold
    legal_hold = Column(
        Boolean, default=False
    )  # True when a legal hold has been applied
    next_action = Column(Text, nullable=True)
    trigger_condition = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    sharepoint_item_id = Column(String, nullable=True)
    sharepoint_list_item_id = Column(String, nullable=True)
    purview_label_applied = Column(Boolean, default=False, nullable=False)
    purview_label_applied_at = Column(DateTime(timezone=True), nullable=True)
    sharepoint_synced_at = Column(DateTime(timezone=True), nullable=True)
    sharepoint_drive_id = Column(String, nullable=True)
    sharepoint_web_url = Column(String, nullable=True)
    sharepoint_last_modified = Column(DateTime(timezone=True), nullable=True)
    processing_status = Column(String, nullable=True, default="pending")
    processing_error = Column(Text, nullable=True)
    processing_attempts = Column(Integer, default=0, nullable=False)
    document_tagged_status = Column(String, nullable=True)
    deletion_status = Column(String, nullable=True, default="Not Deleted")

    document = relationship("DocumentRecord", back_populates="classification")
    sync_logs = relationship(
        "SharePointSyncLog",
        back_populates="classification",
        cascade="all, delete-orphan",
    )


class ProcessingHistory(Base):
    __tablename__ = "processing_history"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    stage = Column(String, nullable=False)
    status = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("DocumentRecord", back_populates="history")


class ReviewRecord(Base):
    """Records each human review decision made on a document."""

    __tablename__ = "review_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id = Column(
        String, nullable=False
    )  # POC: client-provided, not authenticated
    decision = Column(String, nullable=False)  # retain | approve_deletion | reclassify
    comments = Column(Text, nullable=True)
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("DocumentRecord", back_populates="reviews")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    event_type = Column(String, nullable=False)
    event_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("DocumentRecord", back_populates="audit_logs")


class SharePointSyncLog(Base):
    """
    Operational/technical log for each SharePoint sync attempt.

    Tracks each of the (up to) four write steps independently so that partial
    failures are retryable without re-running classification or duplicating rows.

    sync_type values:
      'metadata_columns'   — PATCH driveItem fields on POC_Source_Documents
      'retention_label'    — PATCH /retentionLabel on the driveItem (Purview)
      'retention_list_item' — POST/PATCH row in POC_Documents_Retention list
      'audit_log_entry'    — POST row in POC_classification_auditlog list

    status values: 'success' | 'failed' | 'retrying'
    """

    __tablename__ = "sharepoint_sync_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    classification_record_id = Column(
        String, ForeignKey("classifications.id", ondelete="CASCADE"), nullable=False
    )
    sync_type = Column(
        SAEnum(
            "metadata_columns",
            "retention_label",
            "retention_list_item",
            "audit_log_entry",
            name="sharepoint_sync_type_enum",
        ),
        nullable=False,
    )
    status = Column(
        SAEnum(
            "success",
            "failed",
            "retrying",
            name="sharepoint_sync_status_enum",
        ),
        nullable=False,
    )
    error_message = Column(Text, nullable=True)
    attempted_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    classification = relationship("ClassificationRecord", back_populates="sync_logs")
