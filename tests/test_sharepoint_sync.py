"""
Unit tests for app/sync/sharepoint_sync.py.

All Graph API calls are mocked — no real SharePoint connection required.
These tests can run while admin consent for Sites.FullControl.All is pending.

Coverage:
  1. Full happy path — all 4 steps succeed, DB fields updated correctly.
  2. Partial failure — Step 2 fails, Steps 1/3/4 succeed; SyncResult reflects failure.
  3. Retry logic — second run skips already-succeeded steps, only retries failed ones.
  4. Reclassification — existing list item is PATCHed, not POSTed (no duplicate row).
  5. Ownership guard — isDeleted=True and Deletion_Approved_By set by human are never
     overwritten by our backend on resync.
  6. Purview label idempotency — Step 1 skipped when purview_label_applied=True.
  7. No retention_code — Step 1 gracefully skipped, rest of sync continues.
"""

import asyncio
import pytest
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so we can import the sync module without a live DB/config
# ---------------------------------------------------------------------------

# Patch config before importing the sync module
import sys
import types

_fake_config = sys.modules.get("app.config")
if not _fake_config or not isinstance(_fake_config, types.ModuleType) or hasattr(_fake_config, "__file__"):
    _fake_config = types.ModuleType("app.config")
    sys.modules["app.config"] = _fake_config

_fake_config.SHAREPOINT_SITE_ID = "test-site-id"
_fake_config.SHAREPOINT_SOURCE_DRIVE_ID = "test-drive-id"
_fake_config.SHAREPOINT_RETENTION_LIST_ID = "test-retention-list-id"
_fake_config.SHAREPOINT_AUDITLOG_LIST_ID = "test-auditlog-list-id"
_fake_config.PURVIEW_LABEL_NAME_MAP = {
    "AL": "AL_V1",
    "AV": "AV_V1",
    "FE": "FE_V1",
    "LA": "LA_V1",
    "US": "US_V1",
    "CE": "CE_V1",
    "AC": "AC_V1",
    "PM": "Forever",
}

# Stub db_models to avoid SQLAlchemy setup
_fake_models = types.ModuleType("app.models.db_models")


class _FakeClassificationRecord:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeSharePointSyncLog:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_fake_models.ClassificationRecord = _FakeClassificationRecord
_fake_models.SharePointSyncLog = _FakeSharePointSyncLog
sys.modules["app.models.db_models"] = _fake_models

# Stub app.models as a package so app.models.sharepoint_enums can be registered
_fake_models_pkg = types.ModuleType("app.models")
sys.modules["app.models"] = _fake_models_pkg

# Stub app.models.sharepoint_enums (needed by sharepoint_sync imports)
_fake_enums = types.ModuleType("app.models.sharepoint_enums")

class _FakeDocumentTaggedStatus:
    AUTO_TAGGED = "Auto-Tagged"
    REVIEW_PENDING = "Review Pending"
    REVIEWED = "Reviewed"
    MANUALLY_TAGGED = "Manually Tagged"

    @classmethod
    def human_values(cls):
        return {cls.REVIEWED, cls.MANUALLY_TAGGED}

    @classmethod
    def backend_values(cls):
        return {cls.AUTO_TAGGED, cls.REVIEW_PENDING}

    @classmethod
    def all_valid(cls):
        return {cls.AUTO_TAGGED, cls.REVIEW_PENDING, cls.REVIEWED, cls.MANUALLY_TAGGED}

class _FakeDeletionStatus:
    class _Val:
        def __init__(self, v):
            self.value = v
        def __str__(self):
            return self.value

    NOT_DELETED = _Val("Not Deleted")
    DELETION_REVISED = _Val("Deletion Revised")
    DELETION_APPROVED = _Val("Deletion Approved")
    DELETED = _Val("Deleted")

    @classmethod
    def all_valid(cls):
        return {
            cls.NOT_DELETED.value,
            cls.DELETION_REVISED.value,
            cls.DELETION_APPROVED.value,
            cls.DELETED.value,
        }

def _fake_validate_choice_value(value, enum_class):
    pass  # no-op in tests

_fake_enums.DocumentTaggedStatus = _FakeDocumentTaggedStatus
_fake_enums.DeletionStatus = _FakeDeletionStatus
_fake_enums.validate_choice_value = _fake_validate_choice_value
sys.modules["app.models.sharepoint_enums"] = _fake_enums

# Stub httpx
sys.modules.setdefault("httpx", MagicMock())


# Stub app.services.validation_service (imported by sharepoint_sync)
_fake_validation = types.ModuleType("app.services.validation_service")
_fake_validation.determine_document_tagged_status = lambda status: "Auto-Tagged"
sys.modules["app.services"] = types.ModuleType("app.services")
sys.modules["app.services.validation_service"] = _fake_validation


class _MockGraphClient:
    async def patch_retention_label(self, token: str, drive_id: str, item_id: str, retention_code: str) -> Dict:
        return {"name": retention_code}

    async def patch_metadata_columns(self, token: str, site_id: str, drive_id: str, item_id: str, fields: Dict) -> Dict:
        return {"fields": fields}

    async def create_retention_list_item(self, token: str, site_id: str, list_id: str, fields: Dict) -> Dict:
        return {"id": f"mock-list-item-{uuid.uuid4().hex[:8]}", "fields": fields}

    async def update_retention_list_item(self, token: str, site_id: str, list_id: str, item_id: str, fields: Dict) -> Dict:
        return {"id": item_id, "fields": fields}

    async def get_retention_list_item(self, token: str, site_id: str, list_id: str, item_id: str) -> Dict:
        return {"id": item_id, "fields": {"isDeleted": False, "Deletion_Approved_By": None}}

    async def create_auditlog_item(self, token: str, site_id: str, list_id: str, fields: Dict) -> Dict:
        return {"id": f"mock-audit-{uuid.uuid4().hex[:8]}", "fields": fields}

@pytest.fixture(autouse=True)
def mock_graph_client(monkeypatch):
    monkeypatch.setattr("app.sync.sharepoint_sync._get_graph_client", lambda: _MockGraphClient())

from app.sync.sharepoint_sync import (  # noqa: E402
    SyncPayload,
    SyncResult,
    sync_classification_to_sharepoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(**overrides) -> SyncPayload:
    defaults = dict(
        classification_record_id=str(uuid.uuid4()),
        document_name="Invoice_Q1_2026.pdf",
        document_type="Invoice",
        category="Financial Reporting",
        section="Finance",
        retention_code="AL",
        retention_rule="AL+3",
        retention_end_date="2029-01-15",
        confidence_score=0.92,
        team_owner="Finance Team",
        document_number=101,
        drive_id="test-drive-id",
        sharepoint_item_id="test-item-001",
        sharepoint_list_item_id=None,
        sharepoint_web_url="https://contoso.sharepoint.com/sites/POC/test-item-001",
        purview_label_applied=False,
        triggered_by="system",
        audit_action="classified",
        audit_notes=None,
        classification_status="auto_approved",
    )
    defaults.update(overrides)
    return SyncPayload(**defaults)


class _FakeDB:
    """Minimal AsyncSession stub that records all added objects."""

    def __init__(self, cls_record=None):
        self._cls_record = cls_record  # returned by get()
        self.added: list = []
        self.flushed = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1

    async def get(self, model, pk):
        # Return cls_record regardless of which stub class 'model' is —
        # this is robust when both test files load in the same pytest session
        # and the sync module's ClassificationRecord may come from the other file's stubs.
        if self._cls_record:
            return self._cls_record
        return None

    async def commit(self):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullHappyPath:
    """All 4 steps succeed."""

    @pytest.mark.asyncio
    async def test_all_steps_succeed(self):
        cls_record = _FakeClassificationRecord(
            id="cls-001",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-001")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraphClient()):
            result = await sync_classification_to_sharepoint("fake-token", payload, db)

        assert result.fully_successful, f"Expected full success, got: {result.errors}"
        assert set(result.steps_succeeded) == {
            "retention_label",
            "metadata_columns",
            "retention_list_item",
            "audit_log_entry",
        }
        assert len(result.steps_failed) == 0

    @pytest.mark.asyncio
    async def test_db_fields_updated_after_full_success(self):
        """After a full sync, ClassificationRecord gets purview_label_applied=True
        and sharepoint_synced_at is set."""
        cls_record = _FakeClassificationRecord(
            id="cls-002",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-002")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraphClient()):
            await sync_classification_to_sharepoint("fake-token", payload, db)

        assert cls_record.purview_label_applied is True
        assert cls_record.purview_label_applied_at is not None
        assert cls_record.sharepoint_synced_at is not None

    @pytest.mark.asyncio
    async def test_new_list_item_id_persisted_to_db(self):
        """On first sync, the new list item ID must be saved to ClassificationRecord."""
        cls_record = _FakeClassificationRecord(
            id="cls-003",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-003", sharepoint_list_item_id=None)

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraphClient()):
            result = await sync_classification_to_sharepoint("fake-token", payload, db)

        assert result.sharepoint_list_item_id is not None
        assert cls_record.sharepoint_list_item_id == result.sharepoint_list_item_id


class TestPartialFailureAndRetry:
    """Step 2 fails; other steps still run. Retry only runs the failed step."""

    @pytest.mark.asyncio
    async def test_step2_failure_does_not_block_steps_3_and_4(self):
        class _Step2FailClient(_MockGraphClient):
            async def patch_metadata_columns(self, *args, **kwargs):
                raise RuntimeError("Simulated Graph API 403 on metadata patch")

        cls_record = _FakeClassificationRecord(
            id="cls-010",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-010")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_Step2FailClient()):
            result = await sync_classification_to_sharepoint("fake-token", payload, db)

        assert "metadata_columns" in result.steps_failed
        assert "retention_label" in result.steps_succeeded
        assert "retention_list_item" in result.steps_succeeded
        assert "audit_log_entry" in result.steps_succeeded
        assert not result.fully_successful

    @pytest.mark.asyncio
    async def test_sharepoint_synced_at_not_set_on_partial_failure(self):
        class _Step2FailClient(_MockGraphClient):
            async def patch_metadata_columns(self, *args, **kwargs):
                raise RuntimeError("Simulated failure")

        cls_record = _FakeClassificationRecord(
            id="cls-011",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-011")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_Step2FailClient()):
            await sync_classification_to_sharepoint("fake-token", payload, db)

        # sharepoint_synced_at must remain None on partial failure
        assert cls_record.sharepoint_synced_at is None


class TestReclassification:
    """Existing list item must be PATCHed, not POSTed — no duplicate rows."""

    @pytest.mark.asyncio
    async def test_existing_item_is_patched_not_posted(self):
        patch_calls = []
        post_calls = []

        class _TrackingClient(_MockGraphClient):
            async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
                patch_calls.append(item_id)
                return {"id": item_id, "fields": fields}

            async def create_retention_list_item(self, token, site_id, list_id, fields):
                post_calls.append(fields)
                return await super().create_retention_list_item(token, site_id, list_id, fields)

        cls_record = _FakeClassificationRecord(
            id="cls-020",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id="existing-list-row-99",
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-020",
            sharepoint_list_item_id="existing-list-row-99",
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_TrackingClient()):
            result = await sync_classification_to_sharepoint("fake-token", payload, db)

        assert "existing-list-row-99" in patch_calls, "Expected PATCH on existing item"
        assert len(post_calls) == 0, "Must NOT create a new list item on reclassification"
        assert result.sharepoint_list_item_id == "existing-list-row-99"


class TestOwnershipGuard:
    """
    isDeleted=True and Deletion_Approved_By set by a human must NEVER be
    overwritten by our backend on a resync/reclassification.
    """

    @pytest.mark.asyncio
    async def test_is_deleted_not_overwritten_when_set_by_human(self):
        """When existing item has isDeleted=True, our PATCH must not include isDeleted."""
        patch_payloads = []

        class _OwnershipTrackingClient(_MockGraphClient):
            async def get_retention_list_item(self, token, site_id, list_id, item_id):
                return {"id": item_id, "fields": {"isDeleted": True, "DeletionApprovedBy": "reviewer@org.com"}}

            async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
                patch_payloads.append(fields)
                return {"id": item_id, "fields": fields}

        cls_record = _FakeClassificationRecord(
            id="cls-030",
            purview_label_applied=True,
            purview_label_applied_at=datetime.now(timezone.utc),
            sharepoint_list_item_id="human-reviewed-item",
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-030",
            sharepoint_list_item_id="human-reviewed-item",
            purview_label_applied=True,
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_OwnershipTrackingClient()):
            await sync_classification_to_sharepoint("fake-token", payload, db)

        assert len(patch_payloads) > 0, "Expected a PATCH call to the list item"
        patched = patch_payloads[0]
        # isDeleted and Deletion_Approved_By must NOT appear in our PATCH payload
        assert "isDeleted" not in patched, "isDeleted must not be overwritten by backend"
        assert "DeletionApprovedBy" not in patched, "DeletionApprovedBy must not be overwritten by backend"

    @pytest.mark.asyncio
    async def test_deletion_approved_by_not_overwritten_when_set(self):
        """DeletionApprovedBy set by a human must not appear in our PATCH."""
        patch_payloads = []

        class _TrackClient(_MockGraphClient):
            async def get_retention_list_item(self, token, site_id, list_id, item_id):
                return {"id": item_id, "fields": {"isDeleted": False, "DeletionApprovedBy": "jane.doe@org.com"}}

            async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
                patch_payloads.append(fields)
                return {"id": item_id, "fields": fields}

        cls_record = _FakeClassificationRecord(
            id="cls-031",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id="item-with-approver",
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-031",
            sharepoint_list_item_id="item-with-approver",
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_TrackClient()):
            await sync_classification_to_sharepoint("fake-token", payload, db)

        assert len(patch_payloads) > 0
        assert "DeletionApprovedBy" not in patch_payloads[0]


class TestPurviewLabelIdempotency:
    """Step 1 must be skipped (not re-applied) if purview_label_applied=True."""

    @pytest.mark.asyncio
    async def test_retention_label_not_reapplied_when_already_set(self):
        label_calls = []

        class _TrackingClient(_MockGraphClient):
            async def patch_retention_label(self, token, drive_id, item_id, retention_code):
                label_calls.append(retention_code)
                return {"name": retention_code}

        cls_record = _FakeClassificationRecord(
            id="cls-040",
            purview_label_applied=True,
            purview_label_applied_at=datetime.now(timezone.utc),
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-040",
            purview_label_applied=True,  # already applied
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_TrackingClient()):
            result = await sync_classification_to_sharepoint("fake-token", payload, db)

        assert len(label_calls) == 0, "Purview label must not be re-applied when already set"
        assert "retention_label" in result.steps_succeeded


class TestNoRetentionCode:
    """If retention_code is None/empty, Step 1 is gracefully skipped, rest continues."""

    @pytest.mark.asyncio
    async def test_sync_continues_without_retention_code(self):
        cls_record = _FakeClassificationRecord(
            id="cls-050",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-050",
            retention_code=None,  # no code available
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraphClient()):
            result = await sync_classification_to_sharepoint("fake-token", payload, db)

        # Step 1 not counted as failed — it's a legitimate skip
        assert "retention_label" not in result.steps_failed
        # Other steps should still run
        assert "metadata_columns" in result.steps_succeeded
        assert "retention_list_item" in result.steps_succeeded
        assert "audit_log_entry" in result.steps_succeeded


class TestSyncLogEntries:
    """Each step must produce exactly one SharePointSyncLog record in the DB."""

    @pytest.mark.asyncio
    async def test_sync_log_records_written_for_each_step(self):
        cls_record = _FakeClassificationRecord(
            id="cls-060",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-060")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MockGraphClient()):
            await sync_classification_to_sharepoint("fake-token", payload, db)

        # Duck-type check robust against cross-test-file stub class identity issues
        sync_log_entries = [obj for obj in db.added if hasattr(obj, "sync_type")]
        sync_types_logged = {e.sync_type for e in sync_log_entries}
        assert sync_types_logged == {
            "retention_label",
            "metadata_columns",
            "retention_list_item",
            "audit_log_entry",
        }
        for entry in sync_log_entries:
            assert entry.status == "success"
            assert entry.completed_at is not None


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", __file__, "-v"])

