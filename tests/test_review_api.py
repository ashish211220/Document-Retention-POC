import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.db.database import Base, get_db
from app.models.db_models import DocumentRecord, ClassificationRecord, ReviewRecord, AuditLog
import uuid
import datetime

# Create an in-memory SQLite database for testing
engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def populate_dummy_data(session: AsyncSession):
    doc1 = DocumentRecord(document_id="doc-123", name="test1.pdf", file_type="pdf", page_count=2)
    doc2 = DocumentRecord(document_id="doc-456", name="test2.pdf", file_type="pdf", page_count=5)
    doc3 = DocumentRecord(document_id="doc-789", name="test3.pdf", file_type="pdf", page_count=1)
    
    session.add_all([doc1, doc2, doc3])
    await session.flush()

    cls1 = ClassificationRecord(
        document_id=doc1.id, status="pending_review", confidence_score=0.9,
        category="HR", document_type="Contract", retention_rule="AL+2",
        review_required=True, review_status="pending", legal_hold=False,
        lifecycle_status="Active"
    )
    cls2 = ClassificationRecord(
        document_id=doc2.id, status="completed", confidence_score=0.95,
        category="Finance", document_type="Invoice", retention_rule="FE+4",
        review_required=True, review_status="retained", legal_hold=False,
        lifecycle_status="Active"
    )
    cls3 = ClassificationRecord(
        document_id=doc3.id, status="pending_review", confidence_score=0.4,
        category="Unknown", document_type="Unknown", retention_rule=None,
        review_required=True, review_status="pending", legal_hold=True,
        lifecycle_status="Indeterminate"
    )
    session.add_all([cls1, cls2, cls3])
    await session.commit()

@pytest.mark.asyncio
async def test_get_stats():
    async with TestingSessionLocal() as session:
        await populate_dummy_data(session)
    
    response = client.get("/api/documents/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_documents"] == 3
    assert data["pending_review"] == 2
    assert data["retained"] == 1
    assert data["legal_holds"] == 1

@pytest.mark.asyncio
async def test_get_review_queue():
    async with TestingSessionLocal() as session:
        await populate_dummy_data(session)

    response = client.get("/api/documents/review?review_status=pending")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["retention_rule"] in ["AL+2", None]

@pytest.mark.asyncio
async def test_retain_document():
    async with TestingSessionLocal() as session:
        await populate_dummy_data(session)

    # Retain doc1
    response = client.post(
        "/api/documents/doc-123/review/retain",
        json={"reviewer_id": "user1", "comments": "Looks good"}
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "retain"

    # Verify status changed
    res_get = client.get("/api/documents/doc-123/review")
    assert res_get.json()["review_status"] == "retained"

@pytest.mark.asyncio
async def test_legal_hold_blocks_retain():
    async with TestingSessionLocal() as session:
        await populate_dummy_data(session)

    # Attempt to retain doc3 which has legal_hold=True
    response = client.post(
        "/api/documents/doc-789/review/retain",
        json={"reviewer_id": "user1", "comments": "Try to retain"}
    )
    assert response.status_code == 409
    assert "legal hold is active" in response.json()["detail"].lower()

@pytest.mark.asyncio
async def test_reclassify_document():
    async with TestingSessionLocal() as session:
        await populate_dummy_data(session)

    response = client.post(
        "/api/documents/doc-123/review/reclassify",
        json={"reviewer_id": "user2", "reason": "Wrong category", "suggested_category": "Legal"}
    )
    assert response.status_code == 200
    
    res_get = client.get("/api/documents/doc-123/review")
    assert res_get.json()["review_status"] == "reclassified"

@pytest.mark.asyncio
async def test_toggle_legal_hold():
    async with TestingSessionLocal() as session:
        await populate_dummy_data(session)

    # doc1 initially has legal_hold=False
    response = client.post(
        "/api/documents/doc-123/legal-hold",
        json={"reviewer_id": "user-admin", "apply": True, "comments": "Ongoing lawsuit"}
    )
    assert response.status_code == 200
    
    res_get = client.get("/api/documents/doc-123/review")
    assert res_get.json()["legal_hold"] is True

    # Check audit history for legal hold
    audit_res = client.get("/api/documents/doc-123/audit")
    assert audit_res.status_code == 200
    events = audit_res.json()["audit_events"]
    assert any(e["event_type"] == "LEGAL_HOLD_APPLIED" for e in events)
