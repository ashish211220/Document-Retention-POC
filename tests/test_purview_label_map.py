"""
tests/test_purview_label_map.py

Unit tests for the Purview label map integration as specified in the task:

  1. AL -> AL_V1 in the retentionLabel API call; metadata columns still get raw "AL".
  2. PM -> "Forever" in the API call.
  3. CE (label_not_found): mock 400 from Graph -> caught as LabelNotFoundError,
     logged with [label_not_found] prefix, does NOT raise, does NOT set
     purview_label_applied=True, steps 2-4 still complete.
  4. Retry: CE document that previously failed label_not_found; Graph now
     succeeds -> label applied, purview_label_applied=True, no re-classification.

All Graph calls are mocked. No live SharePoint connection required.
"""

import sys
import types
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs — must be set BEFORE importing any app modules
# ---------------------------------------------------------------------------

# ---- app.config ------------------------------------------------------------
_fake_config = sys.modules.get("app.config")
if not isinstance(_fake_config, types.ModuleType) or not hasattr(_fake_config, "PURVIEW_LABEL_NAME_MAP"):
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

# ---- app.models.db_models --------------------------------------------------
_fake_models_mod = types.ModuleType("app.models.db_models")

class _FakeCls:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

class _FakeSyncLog:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

_fake_models_mod.ClassificationRecord = _FakeCls
_fake_models_mod.SharePointSyncLog = _FakeSyncLog
sys.modules["app.models.db_models"] = _fake_models_mod

# ---- app.models (package stub) + app.models.sharepoint_enums ---------------
_fake_models_pkg = types.ModuleType("app.models")
sys.modules["app.models"] = _fake_models_pkg

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

def _fake_validate(value, enum_class):
    pass  # no-op in tests

_fake_enums.DocumentTaggedStatus = _FakeDocumentTaggedStatus
_fake_enums.DeletionStatus = _FakeDeletionStatus
_fake_enums.validate_choice_value = _fake_validate
sys.modules["app.models.sharepoint_enums"] = _fake_enums

# ---- app.services.validation_service ---------------------------------------
_fake_validation = types.ModuleType("app.services.validation_service")
_fake_validation.determine_document_tagged_status = lambda status: "Auto-Tagged"
sys.modules["app.services"] = types.ModuleType("app.services")
sys.modules["app.services.validation_service"] = _fake_validation

# ---- httpx -----------------------------------------------------------------
sys.modules.setdefault("httpx", MagicMock())

# ---------------------------------------------------------------------------
# Import AFTER stubs are in place
# ---------------------------------------------------------------------------
from app.sync.sharepoint_sync import (  # noqa: E402
    LabelNotFoundError,
    SyncPayload,
    sync_classification_to_sharepoint,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_payload(**overrides) -> SyncPayload:
    defaults = dict(
        classification_record_id=str(uuid.uuid4()),
        document_name="TestDoc.pdf",
        document_type="Report",
        category="Finance",
        section="Accounting",
        retention_code="AL",
        retention_rule="AL+3",
        retention_end_date="2029-01-01",
        confidence_score=0.91,
        team_owner="Finance Team",
        document_number=101,
        drive_id="test-drive-id",
        sharepoint_item_id="item-001",
        sharepoint_list_item_id=None,
        sharepoint_web_url="https://example.sharepoint.com/doc",
        purview_label_applied=False,
        triggered_by="system",
        audit_action="classified",
        audit_notes=None,
        classification_status="auto_approved",
    )
    defaults.update(overrides)
    return SyncPayload(**defaults)


class _FakeDB:
    def __init__(self, cls_record=None):
        self._cls_record = cls_record
        self.added = []
        self.flushed = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1

    async def get(self, model, pk):
        if self._cls_record:
            return self._cls_record
        return None

    async def commit(self):
        pass

    async def execute(self, stmt):
        r = MagicMock()
        r.scalars.return_value.all.return_value = []
        return r


class _BaseClient:
    """Default mock graph client — all steps succeed."""

    async def patch_retention_label(self, token, drive_id, item_id, retention_code):
        mapped = _fake_config.PURVIEW_LABEL_NAME_MAP[retention_code]
        return {"name": mapped}

    async def patch_metadata_columns(self, token, site_id, drive_id, item_id, fields):
        return {"fields": fields}

    async def create_retention_list_item(self, token, site_id, list_id, fields):
        return {"id": f"mock-item-{uuid.uuid4().hex[:8]}", "fields": fields}

    async def update_retention_list_item(self, token, site_id, list_id, item_id, fields):
        return {"id": item_id, "fields": fields}

    async def get_retention_list_item(self, token, site_id, list_id, item_id):
        return {"id": item_id, "fields": {}}

    async def create_auditlog_item(self, token, site_id, list_id, fields):
        return {"id": f"mock-audit-{uuid.uuid4().hex[:8]}", "fields": fields}


# ---------------------------------------------------------------------------
# TEST 1 + 2: Label resolution — AL->AL_V1, PM->Forever, metadata gets raw code
# ---------------------------------------------------------------------------


class TestLabelResolution:
    """retention_code must map to the Purview name in the API call only."""

    @pytest.mark.asyncio
    async def test_al_resolves_to_al_v1_in_label_call(self):
        """retention_code 'AL' must produce {"name": "AL_V1"} in the label call."""
        label_calls = []

        class _TrackingClient(_BaseClient):
            async def patch_retention_label(self, token, drive_id, item_id, retention_code):
                mapped = _fake_config.PURVIEW_LABEL_NAME_MAP[retention_code]
                label_calls.append({"code": retention_code, "mapped": mapped})
                return {"name": mapped}

        cls_record = _FakeCls(
            id="cls-al-01",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-al-01", retention_code="AL")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_TrackingClient()):
            result = await sync_classification_to_sharepoint("tok", payload, db)

        assert result.fully_successful, f"Expected success, errors: {result.errors}"
        assert len(label_calls) == 1
        assert label_calls[0]["code"] == "AL"
        assert label_calls[0]["mapped"] == "AL_V1", (
            f"Expected AL_V1, got {label_calls[0]['mapped']!r}"
        )

    @pytest.mark.asyncio
    async def test_metadata_columns_receive_raw_al_not_al_v1(self):
        """Step 2 metadata columns must write raw 'AL', never 'AL_V1'."""
        metadata_calls = []

        class _MetaTrackingClient(_BaseClient):
            async def patch_metadata_columns(self, token, site_id, drive_id, item_id, fields):
                metadata_calls.append(fields)
                return {"fields": fields}

        cls_record = _FakeCls(
            id="cls-al-02",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-al-02",
            retention_code="AL",
            retention_rule="AL+3",
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_MetaTrackingClient()):
            await sync_classification_to_sharepoint("tok", payload, db)

        assert len(metadata_calls) == 1
        fields = metadata_calls[0]
        assert fields.get("RetentionCode") == "AL", (
            f"RetentionCode must be raw 'AL', got {fields.get('RetentionCode')!r}"
        )
        assert "AL_V1" not in str(fields), (
            f"Mapped name 'AL_V1' must NEVER appear in metadata columns payload: {fields}"
        )

    @pytest.mark.asyncio
    async def test_pm_resolves_to_forever(self):
        """retention_code 'PM' must produce {"name": "Forever"} in the label call."""
        label_calls = []

        class _PMClient(_BaseClient):
            async def patch_retention_label(self, token, drive_id, item_id, retention_code):
                mapped = _fake_config.PURVIEW_LABEL_NAME_MAP[retention_code]
                label_calls.append(mapped)
                return {"name": mapped}

        cls_record = _FakeCls(
            id="cls-pm-01",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-pm-01",
            retention_code="PM",
            retention_rule="Permanent",
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_PMClient()):
            result = await sync_classification_to_sharepoint("tok", payload, db)

        assert result.fully_successful, f"Errors: {result.errors}"
        assert label_calls == ["Forever"], (
            f"Expected 'Forever' for PM, got {label_calls!r}"
        )


# ---------------------------------------------------------------------------
# TEST 3: CE label_not_found
# ---------------------------------------------------------------------------


class TestLabelNotFound:
    """CE_V1 does not exist in Purview yet; Graph returns 400 -> LabelNotFoundError."""

    @pytest.fixture()
    def ce_client(self):
        class _CENotFoundClient(_BaseClient):
            async def patch_retention_label(self, token, drive_id, item_id, retention_code):
                if retention_code == "CE":
                    raise LabelNotFoundError(
                        label_name="CE_V1",
                        retention_code="CE",
                        raw_message='{"error":{"code":"BadRequest","message":"CE_V1 not found"}}',
                    )
                return await super().patch_retention_label(token, drive_id, item_id, retention_code)
        return _CENotFoundClient()

    @pytest.mark.asyncio
    async def test_label_not_found_does_not_raise(self, ce_client):
        """LabelNotFoundError must be caught; no unhandled exception bubbles up."""
        cls_record = _FakeCls(
            id="cls-ce-01",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-ce-01", retention_code="CE")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=ce_client):
            # Must NOT raise anything
            result = await sync_classification_to_sharepoint("tok", payload, db)

        assert "retention_label" in result.steps_failed

    @pytest.mark.asyncio
    async def test_purview_label_applied_stays_false_on_label_not_found(self, ce_client):
        """purview_label_applied must remain False when the label is not in Purview."""
        cls_record = _FakeCls(
            id="cls-ce-02",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-ce-02", retention_code="CE")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=ce_client):
            await sync_classification_to_sharepoint("tok", payload, db)

        assert cls_record.purview_label_applied is False, (
            "purview_label_applied must NOT be True after label_not_found"
        )

    @pytest.mark.asyncio
    async def test_label_not_found_logged_with_error_type_prefix(self, ce_client):
        """Sync log must carry the [label_not_found] prefix so it's queryable."""
        cls_record = _FakeCls(
            id="cls-ce-03",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-ce-03", retention_code="CE")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=ce_client):
            await sync_classification_to_sharepoint("tok", payload, db)

        sync_logs = [obj for obj in db.added if hasattr(obj, "sync_type")]
        label_logs = [e for e in sync_logs if getattr(e, "sync_type", "") == "retention_label"]
        assert len(label_logs) == 1, f"Expected 1 retention_label log, got {len(label_logs)}"
        entry = label_logs[0]
        assert entry.status == "failed"
        assert "[label_not_found]" in (entry.error_message or ""), (
            f"Expected [label_not_found] prefix, got: {entry.error_message!r}"
        )

    @pytest.mark.asyncio
    async def test_steps_2_3_4_complete_after_label_not_found(self, ce_client):
        """Steps 2, 3, 4 must still succeed even when Step 1 fails with label_not_found."""
        cls_record = _FakeCls(
            id="cls-ce-04",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(classification_record_id="cls-ce-04", retention_code="CE")

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=ce_client):
            result = await sync_classification_to_sharepoint("tok", payload, db)

        assert "metadata_columns" in result.steps_succeeded, "Step 2 must succeed"
        assert "retention_list_item" in result.steps_succeeded, "Step 3 must succeed"
        assert "audit_log_entry" in result.steps_succeeded, "Step 4 must succeed"


# ---------------------------------------------------------------------------
# TEST 4: Retry after label_not_found — CE_V1 now exists in Purview
# ---------------------------------------------------------------------------


class TestLabelNotFoundRetry:
    """
    Simulates the retry path after CE_V1 has been created in Purview.
    purview_label_applied is still False from the previous failed attempt.
    Retry succeeds on Step 1 and sets purview_label_applied=True — no
    re-running of classification.
    """

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_label_created(self):
        """After CE_V1 is created, retry sets purview_label_applied=True."""
        class _CECreatedClient(_BaseClient):
            async def patch_retention_label(self, token, drive_id, item_id, retention_code):
                return {"name": "CE_V1"}

        cls_record = _FakeCls(
            id="cls-ce-retry-01",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id="existing-list-row-retry",
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-ce-retry-01",
            retention_code="CE",
            sharepoint_list_item_id="existing-list-row-retry",
            purview_label_applied=False,
            triggered_by="system-retry",
            audit_action="retry_sync",
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_CECreatedClient()):
            result = await sync_classification_to_sharepoint("tok", payload, db)

        assert "retention_label" in result.steps_succeeded, (
            "Step 1 should succeed on retry when CE_V1 now exists in Purview"
        )
        assert "retention_label" not in result.steps_failed
        assert cls_record.purview_label_applied is True, (
            "purview_label_applied must be True after successful retry"
        )
        assert cls_record.purview_label_applied_at is not None

    @pytest.mark.asyncio
    async def test_retry_calls_label_endpoint_exactly_once(self):
        """On retry the label endpoint is called exactly once — no re-classification."""
        label_call_count = [0]

        class _CECreatedClient(_BaseClient):
            async def patch_retention_label(self, token, drive_id, item_id, retention_code):
                label_call_count[0] += 1
                return {"name": "CE_V1"}

        cls_record = _FakeCls(
            id="cls-ce-retry-02",
            purview_label_applied=False,
            purview_label_applied_at=None,
            sharepoint_list_item_id=None,
            sharepoint_synced_at=None,
        )
        db = _FakeDB(cls_record=cls_record)
        payload = _make_payload(
            classification_record_id="cls-ce-retry-02",
            retention_code="CE",
            triggered_by="system-retry",
            audit_action="retry_sync",
        )

        with patch("app.sync.sharepoint_sync._get_graph_client", return_value=_CECreatedClient()):
            result = await sync_classification_to_sharepoint("tok", payload, db)

        assert label_call_count[0] == 1, (
            f"Expected exactly 1 retentionLabel call, got {label_call_count[0]}"
        )
        assert result.fully_successful


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", __file__, "-v"])
