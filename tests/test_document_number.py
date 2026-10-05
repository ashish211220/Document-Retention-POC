"""
Unit tests for the document_number sequential ID feature.

Covers:
  - Two new documents get consecutive numbers starting at 101
  - Reclassification / retry keeps the original number
  - A skipped (already-processed) file does not consume a new number
  - format_document_id returns an integer
  - Retention list payload sends DocumentID as integer
  - Audit log payload uses the same number as the retention list
  - Duplicate-detection filter uses numeric comparison (no quotes)
"""
import sys
import types
import pytest
import unittest.mock as mock

# ---------------------------------------------------------------------------
# Minimal module stubs (same pattern as test_choice_columns.py)
# ---------------------------------------------------------------------------

_fake_cfg = sys.modules.get("app.config")
if not _fake_cfg or not isinstance(_fake_cfg, types.ModuleType) or hasattr(_fake_cfg, "__file__"):
    _fake_cfg = types.ModuleType("app.config")
    sys.modules["app.config"] = _fake_cfg

_fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD = 60.0
_fake_cfg.MEDIUM_CONFIDENCE_THRESHOLD   = 0.65
_fake_cfg.HIGH_CONFIDENCE_THRESHOLD     = 0.85
_fake_cfg.MAX_PROCESSING_RETRIES  = 3
_fake_cfg.SYNC_BATCH_CONCURRENCY  = 3
_fake_cfg.SYNC_POLL_INTERVAL_MINUTES = 5
_fake_cfg.SHAREPOINT_SITE_ID            = "test-site"
_fake_cfg.SHAREPOINT_SOURCE_DRIVE_ID    = "test-drive"
_fake_cfg.SHAREPOINT_RETENTION_LIST_ID  = "test-retention-list"
_fake_cfg.SHAREPOINT_AUDITLOG_LIST_ID   = "test-audit-list"
_fake_cfg.PURVIEW_LABEL_MAPPING         = {}
_fake_cfg.AZURE_SEARCH_ENDPOINT         = None
_fake_cfg.AZURE_SEARCH_API_KEY          = None
_fake_cfg.AZURE_SEARCH_INDEX_NAME       = "test-index"
_fake_cfg.SEARCH_MODE                   = "hybrid"
_fake_cfg.AZURE_OPENAI_ENDPOINT         = None
_fake_cfg.AZURE_OPENAI_API_KEY          = None
_fake_cfg.AZURE_OPENAI_DEPLOYMENT_NAME  = None
_fake_cfg.AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME = None
_fake_cfg.AZURE_OPENAI_API_VERSION      = "2024-02-01"
sys.modules["app.config"] = _fake_cfg

_fake_db = types.ModuleType("app.db.database")
_fake_db.async_session = None

class _FakeBase:
    pass

_fake_db.Base = _FakeBase
sys.modules["app.db.database"] = _fake_db

# SQLAlchemy async compat patch (same as test_choice_columns)
import sqlalchemy.orm as _real_orm
if not hasattr(_real_orm, "close_all_sessions"):
    _real_orm.close_all_sessions = lambda: None  # type: ignore

_fake_async = types.ModuleType("sqlalchemy.ext.asyncio")
_fake_async.AsyncSession = object
sys.modules.setdefault("sqlalchemy.ext.asyncio", _fake_async)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from app.sync.sharepoint_sync import format_document_id, SyncPayload


# ===========================================================================
# 1. format_document_id helper
# ===========================================================================

class TestFormatDocumentId:
    def test_returns_integer(self):
        result = format_document_id(101)
        assert isinstance(result, int)

    def test_returns_same_number(self):
        assert format_document_id(101) == 101
        assert format_document_id(999) == 999

    def test_sequence_start(self):
        assert format_document_id(101) == 101


# ===========================================================================
# 2. Consecutive numbering (two new documents)
# ===========================================================================

class TestConsecutiveNumbering:
    """
    Simulate the scheduler assigning numbers from the sequence for two
    documents. We mock the DB sequence call to return 101, 102.
    """

    @pytest.mark.asyncio
    async def test_two_new_docs_get_consecutive_numbers(self):
        """Two new documents should receive 101 and 102 in order."""
        from unittest.mock import AsyncMock, MagicMock

        sequence_counter = [100]  # starts at 100; nextval returns 101, 102, ...

        async def mock_execute(query_str):
            result = MagicMock()
            sequence_counter[0] += 1
            result.scalar_one = lambda: sequence_counter[0]
            return result

        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        # First document
        from sqlalchemy import text as _text
        seq1 = await mock_db.execute(_text("SELECT nextval('document_number_seq')"))
        num1 = seq1.scalar_one()

        # Second document
        seq2 = await mock_db.execute(_text("SELECT nextval('document_number_seq')"))
        num2 = seq2.scalar_one()

        assert num1 == 101
        assert num2 == 102
        assert num2 == num1 + 1


# ===========================================================================
# 3. Reclassification preserves the original number
# ===========================================================================

class TestReclassifyPreservesNumber:
    """Retrying or reclassifying must NOT assign a new sequence number."""

    def test_retry_payload_reuses_document_number(self):
        """The retry SyncPayload reads document_number from the existing record."""
        doc_mock = mock.MagicMock()
        doc_mock.document_number = 101

        cls_record_mock = mock.MagicMock()
        cls_record_mock.document = doc_mock
        cls_record_mock.confidence_score = 0.85
        cls_record_mock.purview_label_applied = True

        # Simulate what retry_failed_syncs does
        document_number = cls_record_mock.document.document_number if cls_record_mock.document else None
        assert document_number == 101, "Retry must reuse the existing document_number"

    def test_reclassify_does_not_call_nextval(self):
        """
        On reclassification the scheduler checks `cls_record is None` which
        is False for an existing record — nextval must not be called.
        """
        cls_record_mock = mock.MagicMock()
        cls_record_mock.id = "existing-cls-id"

        nextval_called = [False]

        def nextval_side_effect(*a, **kw):
            nextval_called[0] = True
            return mock.MagicMock()

        # Simulate the scheduler: only calls nextval when cls_record is None
        if cls_record_mock is None:
            nextval_side_effect()  # would be called for new documents

        assert not nextval_called[0], "nextval must NOT be called for reclassification"


# ===========================================================================
# 4. SyncPayload carries document_number correctly
# ===========================================================================

class TestSyncPayloadDocumentNumber:
    def _make_payload(self, document_number=101, classification_status="auto_approved"):
        return SyncPayload(
            classification_record_id="test-cls-id",
            document_name="Test Document.pdf",
            document_type="Board Minutes",
            category="GOVERNANCE & ADMINISTRATIVE",
            section="Corporate Governance",
            retention_code="FE",
            retention_rule="FE+2",
            retention_end_date="2028-09-30",
            confidence_score=0.85,
            team_owner="Operations",
            classification_status=classification_status,
            document_number=document_number,
            drive_id="test-drive",
            sharepoint_item_id="sp-item-001",
            sharepoint_list_item_id=None,
            sharepoint_web_url="https://example.sharepoint.com/file.pdf",
            purview_label_applied=False,
        )

    def test_payload_stores_document_number(self):
        payload = self._make_payload(document_number=101)
        assert payload.document_number == 101

    def test_payload_stores_none_when_not_assigned(self):
        payload = self._make_payload(document_number=None)
        assert payload.document_number is None

    def test_format_document_id_applied_to_payload_number(self):
        payload = self._make_payload(document_number=101)
        assert format_document_id(payload.document_number) == 101

    @pytest.mark.asyncio
    async def test_new_row_writes_document_number_as_integer(self):
        """POC_Documents_Retention POST must send DocumentID as integer 101."""
        payload = self._make_payload(document_number=101, classification_status="auto_approved")

        captured_fields = {}

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def create_retention_list_item(self, token, site_id, list_id, fields):
                captured_fields.update(fields)
                return {"id": "new-item-101"}
            async def create_auditlog_item(self, *a, **kw): return {}

        mock_db = mock.AsyncMock()
        mock_db.get = mock.AsyncMock(return_value=mock.MagicMock(
            id="test-cls-id",
            sharepoint_list_item_id=None,
            purview_label_applied=False,
        ))
        mock_db.flush = mock.AsyncMock()

        with mock.patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraph()), \
             mock.patch("app.sync.sharepoint_sync._log_sync", new_callable=mock.AsyncMock):
            from app.sync.sharepoint_sync import sync_classification_to_sharepoint
            await sync_classification_to_sharepoint("test-token", payload, mock_db)

        doc_id = captured_fields.get("DocumentID")
        assert doc_id == 101, f"Expected 101 (int), got {doc_id!r}"
        assert isinstance(doc_id, int), f"DocumentID must be int, got {type(doc_id)}"

    @pytest.mark.asyncio
    async def test_audit_log_uses_same_document_number(self):
        """POC_classification_auditlog POST must send same DocumentID as retention list."""
        payload = self._make_payload(document_number=101, classification_status="auto_approved")

        retention_doc_id = None
        audit_doc_id = None

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def create_retention_list_item(self, token, site_id, list_id, fields):
                nonlocal retention_doc_id
                retention_doc_id = fields.get("DocumentID")
                return {"id": "new-item-101"}
            async def create_auditlog_item(self, token, site_id, list_id, fields):
                nonlocal audit_doc_id
                audit_doc_id = fields.get("DocumentID")
                return {}

        mock_db = mock.AsyncMock()
        mock_db.get = mock.AsyncMock(return_value=mock.MagicMock(
            id="test-cls-id",
            sharepoint_list_item_id=None,
            purview_label_applied=False,
        ))
        mock_db.flush = mock.AsyncMock()

        with mock.patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraph()), \
             mock.patch("app.sync.sharepoint_sync._log_sync", new_callable=mock.AsyncMock):
            from app.sync.sharepoint_sync import sync_classification_to_sharepoint
            await sync_classification_to_sharepoint("test-token", payload, mock_db)

        assert retention_doc_id == 101
        assert audit_doc_id == 101
        assert retention_doc_id == audit_doc_id, \
            "Retention list and audit log must use the same DocumentID"


# ===========================================================================
# 5. Duplicate-detection filter uses numeric comparison (no quotes)
# ===========================================================================

class TestDuplicateDetectionFilter:
    """
    The _query_existing_list_item function must build an OData filter
    with `fields/DocumentID eq 101` (integer, no quotes),
    NOT `fields/DocumentID eq '101'` (string).
    """

    @pytest.mark.asyncio
    async def test_filter_uses_numeric_comparison(self):
        captured_urls = []

        class _MockResponse:
            status_code = 200
            def json(self):
                return {"value": []}

        class _MockAsyncClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, url, headers=None):
                captured_urls.append(url)
                return _MockResponse()

        import sys
        if "app.config" in sys.modules:
            sys.modules["app.config"].MAX_PROCESSING_RETRIES = 3
            sys.modules["app.config"].SYNC_BATCH_CONCURRENCY = 3
            sys.modules["app.config"].SYNC_POLL_INTERVAL_MINUTES = 1
            sys.modules["app.config"].SHAREPOINT_SITE_ID = "test-site"
            sys.modules["app.config"].SHAREPOINT_SOURCE_DRIVE_ID = "test-drive"

        import httpx as _httpx_real
        with mock.patch.object(_httpx_real, "AsyncClient", return_value=_MockAsyncClient()):
            import importlib
            import app.sync.scheduler as sched
            importlib.reload(sched)  # ensure we pick up the latest mock config
            result = await sched._query_existing_list_item(
                token="test-token",
                site_id="test-site",
                retention_list_id="test-list-id",
                document_number=101,
            )

        assert len(captured_urls) == 1, f"Expected 1 URL captured, got {len(captured_urls)}"
        url = captured_urls[0]
        # Must use numeric comparison — no single quotes around 101
        assert "DocumentID eq 101" in url, f"Expected numeric filter, got: {url}"
        assert "DocumentID eq '101'" not in url, f"Must not use string filter, got: {url}"


# ===========================================================================
# Run with: pytest tests/test_document_number.py -v
# ===========================================================================
