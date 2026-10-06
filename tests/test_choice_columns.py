
import os
import sys
import types
import pytest
import unittest.mock as mock

# ---------------------------------------------------------------------------
# Minimal module stubs — inject before importing app modules
# ---------------------------------------------------------------------------

# Stub app.config (overridden per test where needed)
_fake_cfg = sys.modules.get("app.config")
if not _fake_cfg or not isinstance(_fake_cfg, types.ModuleType) or hasattr(_fake_cfg, "__file__"):
    _fake_cfg = types.ModuleType("app.config")
    sys.modules["app.config"] = _fake_cfg

_fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD = 60.0   # 0-100 scale, default
_fake_cfg.MEDIUM_CONFIDENCE_THRESHOLD   = 0.65   # 0-1 scale, unchanged
_fake_cfg.HIGH_CONFIDENCE_THRESHOLD     = 0.85   # kept for compat
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

# Stub DB
_fake_db = types.ModuleType("app.db.database")
_fake_db.async_session = None


# Stub sqlalchemy.orm partially (avoid interfering with sqlalchemy.ext.asyncio)
class _FakeBase:
    pass


_fake_db.Base = _FakeBase
sys.modules["app.db.database"] = _fake_db

# ---------------------------------------------------------------------------
# Real imports (after stubs)
# ---------------------------------------------------------------------------
from app.models.sharepoint_enums import (
    DocumentTaggedStatus,
    DeletionStatus,
    validate_choice_value,
)
from app.services.validation_service import (
    determine_status,
    determine_document_tagged_status,
)


# ===========================================================================
# 1. Enum correctness
# ===========================================================================

class TestDocumentTaggedStatusEnum:
    def test_exact_strings(self):
        assert DocumentTaggedStatus.AUTO_TAGGED.value    == "Auto-Tagged"
        assert DocumentTaggedStatus.REVIEW_PENDING.value == "Review Pending"
        assert DocumentTaggedStatus.REVIEWED.value       == "Reviewed"
        assert DocumentTaggedStatus.MANUALLY_TAGGED.value == "Manually Tagged"

    def test_backend_values(self):
        assert DocumentTaggedStatus.backend_values() == {"Auto-Tagged", "Review Pending"}

    def test_human_values(self):
        assert DocumentTaggedStatus.human_values() == {"Reviewed", "Manually Tagged"}

    def test_all_valid(self):
        assert DocumentTaggedStatus.all_valid() == {
            "Auto-Tagged", "Review Pending", "Reviewed", "Manually Tagged"
        }


class TestDeletionStatusEnum:
    def test_exact_strings(self):
        assert DeletionStatus.NOT_DELETED.value       == "Not Deleted"
        assert DeletionStatus.DELETION_REVISED.value  == "Deletion Revised"
        assert DeletionStatus.DELETION_APPROVED.value == "Deletion Approved"
        assert DeletionStatus.DELETED.value           == "Deleted"

    def test_all_valid(self):
        assert DeletionStatus.all_valid() == {
            "Not Deleted", "Deletion Revised", "Deletion Approved", "Deleted"
        }


# ===========================================================================
# 2. validate_choice_value
# ===========================================================================

class TestValidateChoiceValue:
    def test_valid_tagged_values_pass(self):
        for v in DocumentTaggedStatus.all_valid():
            validate_choice_value(v, DocumentTaggedStatus)  # must not raise

    def test_invalid_tagged_value_raises(self):
        with pytest.raises(ValueError, match="Invalid choice value"):
            validate_choice_value("auto tagged", DocumentTaggedStatus)    # wrong case

    def test_boolean_string_rejected(self):
        with pytest.raises(ValueError, match="Invalid choice value"):
            validate_choice_value("False", DeletionStatus)

    def test_valid_deletion_values_pass(self):
        for v in DeletionStatus.all_valid():
            validate_choice_value(v, DeletionStatus)  # must not raise

    def test_invalid_deletion_raises(self):
        with pytest.raises(ValueError, match="Invalid choice value"):
            validate_choice_value("deleted", DeletionStatus)  # wrong case


# ===========================================================================
# 3. Confidence threshold routing — boundary tests
# ===========================================================================

class TestDetermineStatus:
    """
    AUTO_TAG_CONFIDENCE_THRESHOLD = 60 (0-100 scale).
    Scores fed to determine_status are on 0-1 scale.
    Single normalization: score * 100 >= 60.
    """

    def _score(self, pct: float) -> float:
        """Convert a 0-100 percentage to a 0-1 score."""
        return pct / 100

    def test_below_threshold_is_pending(self):
        assert determine_status(self._score(59.9)) == "pending_review"

    def test_at_threshold_is_auto_approved(self):
        assert determine_status(self._score(60.0)) == "auto_approved"

    def test_above_threshold_is_auto_approved(self):
        assert determine_status(self._score(60.1)) == "auto_approved"

    def test_zero_score_is_pending(self):
        assert determine_status(0.0) == "pending_review"

    def test_perfect_score_is_auto_approved(self):
        assert determine_status(1.0) == "auto_approved"

    def test_0_1_scale_input_at_threshold(self):
        """0.60 on a 0-1 scale == 60 on a 0-100 scale → auto_approved."""
        assert determine_status(0.60) == "auto_approved"

    def test_0_1_scale_input_below_threshold(self):
        """0.599 on a 0-1 scale < 60 on a 0-100 scale → pending_review."""
        assert determine_status(0.599) == "pending_review"


class TestDetermineStatusEnvChange:
    """Changing AUTO_TAG_CONFIDENCE_THRESHOLD via env changes routing without code edit."""

    def test_higher_threshold_makes_60_fail(self):
        original = _fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD
        try:
            _fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD = 70.0
            # 0.60 * 100 = 60, which is < 70 → should NOT be auto_approved
            result = determine_status(0.60)
            assert result != "auto_approved", (
                "Expected 0.60 to not be auto_approved when threshold=70"
            )
        finally:
            _fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD = original

    def test_lower_threshold_makes_50_pass(self):
        original = _fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD
        try:
            _fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD = 50.0
            # 0.50 * 100 = 50 >= 50 → auto_approved
            assert determine_status(0.50) == "auto_approved"
        finally:
            _fake_cfg.AUTO_TAG_CONFIDENCE_THRESHOLD = original


# ===========================================================================
# 4. determine_document_tagged_status
# ===========================================================================

class TestDetermineDocumentTaggedStatus:
    def test_auto_approved_maps_to_auto_tagged(self):
        assert determine_document_tagged_status("auto_approved") == "Auto-Tagged"

    def test_pending_review_maps_to_review_pending(self):
        assert determine_document_tagged_status("pending_review") == "Review Pending"

    def test_review_recommended_maps_to_review_pending(self):
        assert determine_document_tagged_status("review_recommended") == "Review Pending"

    def test_unknown_status_maps_to_review_pending(self):
        assert determine_document_tagged_status("unknown_status") == "Review Pending"


# ===========================================================================
# 5. Sync payload tests (mocked Graph + DB)
# ===========================================================================

# Inject minimal stubs so `sharepoint_sync` can be imported without a live
# SQLAlchemy async engine. These stubs extend the ones already in sys.modules.
import sqlalchemy.orm as _real_orm
if not hasattr(_real_orm, "close_all_sessions"):
    _real_orm.close_all_sessions = lambda: None  # type: ignore[attr-defined]

# Stub the async session module expected by sharepoint_sync
_fake_async = types.ModuleType("sqlalchemy.ext.asyncio")
_fake_async.AsyncSession = object
sys.modules.setdefault("sqlalchemy.ext.asyncio", _fake_async)


class TestSyncPayloadChoiceValues:
    """
    Test the actual SharePoint sync field construction by inspecting what
    fields are passed to graph.create_retention_list_item and
    graph.update_retention_list_item.
    """

    def _make_payload(self, classification_status="auto_approved"):
        from app.sync.sharepoint_sync import SyncPayload
        return SyncPayload(
            classification_record_id="test-cls-id",
            document_name="Test Document.pdf",
            document_type="Board Minutes",
            category="GOVERNANCE & ADMINISTRATIVE",
            section="Corporate Governance",
            retention_code="FE",
            retention_rule="FE+2",
            retention_end_date="2028-09-30",
            confidence_score=0.82,
            team_owner="Operations",
            classification_status=classification_status,
            document_number=101,
            drive_id="test-drive",
            sharepoint_item_id="sp-item-001",
            sharepoint_list_item_id=None,
            sharepoint_web_url="https://example.sharepoint.com/file.pdf",
            purview_label_applied=False,
        )

    @pytest.mark.asyncio
    async def test_new_row_writes_auto_tagged_and_not_deleted(self):
        """Creating a new row should write DocumentTagged='Auto-Tagged' and isDeleted='Not Deleted'."""
        payload = self._make_payload("auto_approved")

        captured_fields = {}

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def create_retention_list_item(self, token, site_id, list_id, fields):
                captured_fields.update(fields)
                return {"id": "new-item-123"}
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

        assert captured_fields.get("DocumentTagged") == "Auto-Tagged"
        assert captured_fields.get("isDeleted") == "Not Deleted"

    @pytest.mark.asyncio
    async def test_new_row_low_confidence_writes_review_pending(self):
        """Low confidence new row should write DocumentTagged='Review Pending'."""
        payload = self._make_payload("pending_review")

        captured_fields = {}

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def create_retention_list_item(self, token, site_id, list_id, fields):
                captured_fields.update(fields)
                return {"id": "new-item-456"}
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

        assert captured_fields.get("DocumentTagged") == "Review Pending"

    @pytest.mark.asyncio
    async def test_resync_does_not_include_is_deleted(self):
        """PATCH payload on resync must never contain isDeleted."""
        from app.sync.sharepoint_sync import SyncPayload
        payload = SyncPayload(
            classification_record_id="test-cls-id",
            document_name="Test.pdf",
            document_type="Board Minutes",
            category="GOVERNANCE & ADMINISTRATIVE",
            section="Corporate Governance",
            retention_code="FE",
            retention_rule="FE+2",
            retention_end_date="2028-09-30",
            confidence_score=0.82,
            team_owner="Operations",
            classification_status="auto_approved",
            document_number=101,
            drive_id="test-drive",
            sharepoint_item_id="sp-item-001",
            sharepoint_list_item_id="existing-item-789",   # existing → triggers PATCH
            sharepoint_web_url="https://example.sharepoint.com/file.pdf",
            purview_label_applied=True,
        )

        patched_fields = {}

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def get_retention_list_item(self, *a, **kw):
                return {"fields": {"DocumentTagged": "Auto-Tagged", "isDeleted": "Not Deleted"}}
            async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
                patched_fields.update(fields)
                return {}
            async def create_auditlog_item(self, *a, **kw): return {}

        mock_db = mock.AsyncMock()
        mock_db.get = mock.AsyncMock(return_value=None)
        mock_db.flush = mock.AsyncMock()

        with mock.patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraph()), \
             mock.patch("app.sync.sharepoint_sync._log_sync", new_callable=mock.AsyncMock):
            from app.sync.sharepoint_sync import sync_classification_to_sharepoint
            await sync_classification_to_sharepoint("test-token", payload, mock_db)

        assert "isDeleted" not in patched_fields, \
            f"isDeleted must not appear in PATCH payload, got keys: {list(patched_fields.keys())}"

    @pytest.mark.asyncio
    async def test_resync_does_not_overwrite_reviewed(self):
        """If existing DocumentTagged='Reviewed', PATCH must NOT include DocumentTagged."""
        from app.sync.sharepoint_sync import SyncPayload
        payload = SyncPayload(
            classification_record_id="test-cls-id",
            document_name="Test.pdf",
            document_type="Board Minutes",
            category="GOVERNANCE & ADMINISTRATIVE",
            section="Corporate Governance",
            retention_code="FE",
            retention_rule="FE+2",
            retention_end_date="2028-09-30",
            confidence_score=0.90,
            team_owner="Operations",
            classification_status="auto_approved",
            document_number=101,
            drive_id="test-drive",
            sharepoint_item_id="sp-item-001",
            sharepoint_list_item_id="existing-item-789",
            sharepoint_web_url="https://example.sharepoint.com/file.pdf",
            purview_label_applied=True,
        )

        patched_fields = {}

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def get_retention_list_item(self, *a, **kw):
                # Human has set this to 'Reviewed'
                return {"fields": {"DocumentTagged": "Reviewed", "isDeleted": "Not Deleted"}}
            async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
                patched_fields.update(fields)
                return {}
            async def create_auditlog_item(self, *a, **kw): return {}

        mock_db = mock.AsyncMock()
        mock_db.get = mock.AsyncMock(return_value=None)
        mock_db.flush = mock.AsyncMock()

        with mock.patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraph()), \
             mock.patch("app.sync.sharepoint_sync._log_sync", new_callable=mock.AsyncMock):
            from app.sync.sharepoint_sync import sync_classification_to_sharepoint
            await sync_classification_to_sharepoint("test-token", payload, mock_db)

        assert "DocumentTagged" not in patched_fields, \
            f"DocumentTagged must not be in PATCH when human-set to 'Reviewed'. Got: {patched_fields}"
        assert "isDeleted" not in patched_fields

    @pytest.mark.asyncio
    async def test_resync_does_not_overwrite_manually_tagged(self):
        """If existing DocumentTagged='Manually Tagged', PATCH must NOT include DocumentTagged."""
        from app.sync.sharepoint_sync import SyncPayload
        payload = SyncPayload(
            classification_record_id="test-cls-id",
            document_name="Test.pdf",
            document_type="Board Minutes",
            category="GOVERNANCE & ADMINISTRATIVE",
            section="Corporate Governance",
            retention_code="FE",
            retention_rule="FE+2",
            retention_end_date="2028-09-30",
            confidence_score=0.90,
            team_owner="Operations",
            classification_status="auto_approved",
            document_number=101,
            drive_id="test-drive",
            sharepoint_item_id="sp-item-001",
            sharepoint_list_item_id="existing-item-789",
            sharepoint_web_url="https://example.sharepoint.com/file.pdf",
            purview_label_applied=True,
        )

        patched_fields = {}

        class _MockGraph:
            async def patch_retention_label(self, *a, **kw): return {}
            async def patch_metadata_columns(self, *a, **kw): return {}
            async def get_retention_list_item(self, *a, **kw):
                return {"fields": {"DocumentTagged": "Manually Tagged"}}
            async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
                patched_fields.update(fields)
                return {}
            async def create_auditlog_item(self, *a, **kw): return {}

        mock_db = mock.AsyncMock()
        mock_db.get = mock.AsyncMock(return_value=None)
        mock_db.flush = mock.AsyncMock()

        with mock.patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraph()), \
             mock.patch("app.sync.sharepoint_sync._log_sync", new_callable=mock.AsyncMock):
            from app.sync.sharepoint_sync import sync_classification_to_sharepoint
            await sync_classification_to_sharepoint("test-token", payload, mock_db)

        assert "DocumentTagged" not in patched_fields


# ===========================================================================
# Run with: pytest tests/test_choice_columns.py -v
# ===========================================================================
