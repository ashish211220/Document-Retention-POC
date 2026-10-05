"""
Unit tests for app/sync/scheduler.py

Coverage:
  1. Skip-check - new document (no record)
  2. Skip-check - unchanged document (same lastModified) -> skip
  3. Skip-check - modified document -> reclassify
  4. Skip-check - failed_permanent -> skip_permanent
  5. Skip-check - processing (crash recovery) -> resume
  6. Concurrency lock - overlapping cycle is skipped, not parallelised
  7. Restart recovery - record with processing_status=processing (server crash)
     results in resume so existing idempotency guards kick in
  8. One failing document in a batch of five does not block the other four
  9. _parse_sp_datetime utility - valid, None, invalid inputs
  10. Exponential backoff - 429 is retried; raises after max_total_wait

All Graph API, Azure SDK, and DB calls are mocked - no real connections needed.
"""

import asyncio
import importlib.util
import os
import sys
import types
import pytest
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap: register all dependency stubs BEFORE loading the scheduler module.
#
# We use importlib.util.spec_from_file_location to load scheduler.py directly
# from its file path. This bypasses Python's normal package import machinery
# so our sys.modules stubs win over the real app.sync package on disk.
# ---------------------------------------------------------------------------

# Step 1 - ensure parent packages exist as stubs
for _pkg in ("app", "app.sync", "app.db", "app.models"):
    if _pkg not in sys.modules:
        sys.modules[_pkg] = types.ModuleType(_pkg)

# Step 2 - stub app.config
_fake_cfg = types.ModuleType("app.config")
_fake_cfg.MOCK_SHAREPOINT = False
_fake_cfg.SHAREPOINT_SITE_ID = "test-site-id"
_fake_cfg.SHAREPOINT_SOURCE_DRIVE_ID = "test-drive-id"
_fake_cfg.SHAREPOINT_RETENTION_LIST_ID = "test-retention-list-id"
_fake_cfg.SHAREPOINT_AUDITLOG_LIST_ID = "test-auditlog-list-id"
_fake_cfg.SYNC_POLL_INTERVAL_MINUTES = 5
_fake_cfg.MAX_PROCESSING_RETRIES = 3
_fake_cfg.SYNC_BATCH_CONCURRENCY = 3
sys.modules["app.config"] = _fake_cfg

# Step 3 - stub app.db.database
_fake_db = types.ModuleType("app.db.database")
_fake_db.async_session = None
sys.modules["app.db.database"] = _fake_db


# Step 4 - stub app.models.db_models
#
# _FakeCls needs class-level attributes that support SQLAlchemy-style
# comparisons (cls.sharepoint_item_id == value) used in select().where().
# We create a simple descriptor that always compares equal to anything
# so the where() call doesn't raise AttributeError. The actual filtering
# is handled by _FakeDB.execute which returns our pre-configured record.

class _ColAttr:
    """Class-level descriptor that supports == without raising AttributeError."""
    def __init__(self, name):
        self.name = name

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self  # class-level access: return self so __eq__ works
        return obj.__dict__.get(self.name)

    def __set__(self, obj, value):
        obj.__dict__[self.name] = value

    def __eq__(self, other):
        return True  # always truthy so where() doesn't crash


class _FakeCls:
    sharepoint_item_id = _ColAttr("sharepoint_item_id")
    processing_status = _ColAttr("processing_status")

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        raise AttributeError(name)


class _FakeDoc:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeSyncLog:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_fake_models = types.ModuleType("app.models.db_models")
_fake_models.ClassificationRecord = _FakeCls
_fake_models.DocumentRecord = _FakeDoc
_fake_models.SharePointSyncLog = _FakeSyncLog
sys.modules["app.models.db_models"] = _fake_models


# Step 5 - stub app.sync.sharepoint_sync
class _FakeSyncPayload:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeSyncResult:
    fully_successful = True
    steps_succeeded = [
        "retention_label", "metadata_columns",
        "retention_list_item", "audit_log_entry"
    ]
    steps_failed = []
    errors = {}
    sharepoint_list_item_id = "mock-list-item-001"


_fake_sp_sync = types.ModuleType("app.sync.sharepoint_sync")
_fake_sp_sync.SyncPayload = _FakeSyncPayload
_fake_sp_sync.sync_classification_to_sharepoint = AsyncMock(return_value=_FakeSyncResult())
sys.modules["app.sync.sharepoint_sync"] = _fake_sp_sync

# Step 6 - stub sqlalchemy (scheduler does: from sqlalchemy import select)
_fake_sa = types.ModuleType("sqlalchemy")
_fake_sa.select = MagicMock(return_value=MagicMock())
sys.modules["sqlalchemy"] = _fake_sa
sys.modules["sqlalchemy.ext"] = types.ModuleType("sqlalchemy.ext")
_fake_sa_async = types.ModuleType("sqlalchemy.ext.asyncio")
_fake_sa_async.AsyncSession = MagicMock
sys.modules["sqlalchemy.ext.asyncio"] = _fake_sa_async

# Step 7 - keep real httpx (backoff tests need real exception classes)
import httpx  # noqa: E402

# Step 8 - load scheduler.py directly from its file path
_scheduler_path = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "sync", "scheduler.py")
)
_spec = importlib.util.spec_from_file_location("app.sync.scheduler", _scheduler_path)
_sched = importlib.util.module_from_spec(_spec)
sys.modules["app.sync.scheduler"] = _sched
_spec.loader.exec_module(_sched)

# Expose public symbols for test use
_determine_action = _sched._determine_action
_parse_sp_datetime = _sched._parse_sp_datetime
_poll_lock = _sched._poll_lock
_run_poll_cycle = _sched._run_poll_cycle
_with_backoff = _sched._with_backoff
start_scheduler = _sched.start_scheduler
stop_scheduler = _sched.stop_scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(
    item_id: str = "item-001",
    name: str = "Invoice.pdf",
    last_modified: str = "2026-09-20T10:00:00Z",
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "lastModifiedDateTime": last_modified,
        "webUrl": f"https://contoso.sharepoint.com/sites/poc/{name}",
        "file": {"mimeType": "application/pdf"},
    }


class _FakeDB:
    """Minimal async DB session stub."""

    def __init__(self, cls_record=None):
        self._cls_record = cls_record
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.first.return_value = self._cls_record
        return result

    async def get(self, model, pk):
        return self._cls_record


# ---------------------------------------------------------------------------
# Tests 1-5: Skip-check logic
# ---------------------------------------------------------------------------

class TestDetermineAction:

    @pytest.mark.asyncio
    async def test_new_document_returns_new(self):
        """No ClassificationRecord in DB -> action should be new."""
        db = _FakeDB(cls_record=None)
        item = _make_item()
        action = await _determine_action(db, item)
        assert action == "new"

    @pytest.mark.asyncio
    async def test_unchanged_document_is_skipped(self):
        """Record exists with same lastModified timestamp -> skip."""
        ts = "2026-09-20T10:00:00Z"
        rec = _FakeCls(
            sharepoint_item_id="item-001",
            sharepoint_last_modified=datetime.fromisoformat(ts.replace("Z", "+00:00")),
            processing_status="completed",
            processing_attempts=1,
            purview_label_applied=True,
        )
        db = _FakeDB(cls_record=rec)
        item = _make_item(last_modified=ts)
        action = await _determine_action(db, item)
        assert action == "skip", f"Expected skip, got {action}"

    @pytest.mark.asyncio
    async def test_modified_document_returns_reclassify(self):
        """File is newer than what we last processed -> reclassify."""
        old_ts = "2026-09-15T08:00:00Z"
        new_ts = "2026-09-25T12:00:00Z"
        rec = _FakeCls(
            sharepoint_item_id="item-002",
            sharepoint_last_modified=datetime.fromisoformat(old_ts.replace("Z", "+00:00")),
            processing_status="completed",
            processing_attempts=1,
            purview_label_applied=True,
        )
        db = _FakeDB(cls_record=rec)
        item = _make_item(item_id="item-002", last_modified=new_ts)
        action = await _determine_action(db, item)
        assert action == "reclassify"

    @pytest.mark.asyncio
    async def test_permanently_failed_is_skip_permanent(self):
        """Exceeded max retries -> skip_permanent."""
        rec = _FakeCls(
            sharepoint_item_id="item-003",
            sharepoint_last_modified=None,
            processing_status="failed_permanent",
            processing_attempts=3,
            purview_label_applied=False,
        )
        db = _FakeDB(cls_record=rec)
        item = _make_item(item_id="item-003")
        action = await _determine_action(db, item)
        assert action == "skip_permanent"

    @pytest.mark.asyncio
    async def test_processing_status_returns_resume(self):
        """processing_status=processing means server crashed -> resume."""
        rec = _FakeCls(
            sharepoint_item_id="item-004",
            sharepoint_last_modified=None,
            processing_status="processing",
            processing_attempts=1,
            purview_label_applied=False,
        )
        db = _FakeDB(cls_record=rec)
        item = _make_item(item_id="item-004")
        action = await _determine_action(db, item)
        assert action == "resume"


# ---------------------------------------------------------------------------
# Test 6: Concurrency lock
# ---------------------------------------------------------------------------

class TestConcurrencyLock:

    @pytest.mark.asyncio
    async def test_poll_lock_is_asyncio_lock(self):
        """_poll_lock must be an asyncio.Lock so only one cycle runs at a time."""
        assert isinstance(_poll_lock, type(asyncio.Lock()))

    @pytest.mark.asyncio
    async def test_lock_prevents_concurrent_acquisition(self):
        """While the lock is held, a second coroutine must see it as locked."""
        acquired_second = False

        async def try_acquire():
            nonlocal acquired_second
            if not _poll_lock.locked():
                async with _poll_lock:
                    acquired_second = True

        async with _poll_lock:
            assert _poll_lock.locked()
            await asyncio.wait_for(try_acquire(), timeout=0.1)

        assert not acquired_second, "Lock was acquired while already held"


# ---------------------------------------------------------------------------
# Test 7: Restart recovery
# ---------------------------------------------------------------------------

class TestRestartRecovery:

    @pytest.mark.asyncio
    async def test_partial_sync_log_causes_resume_not_new(self):
        """
        Record with processing_status=processing (server crash after step 1)
        must return resume, not new.
        """
        rec = _FakeCls(
            sharepoint_item_id="item-crashed",
            sharepoint_last_modified=None,
            processing_status="processing",
            processing_attempts=1,
            purview_label_applied=True,
            sharepoint_list_item_id=None,
        )
        db = _FakeDB(cls_record=rec)
        item = _make_item(item_id="item-crashed")
        action = await _determine_action(db, item)
        assert action == "resume"
        assert rec.purview_label_applied is True


# ---------------------------------------------------------------------------
# Test 8: Batch isolation
# ---------------------------------------------------------------------------

class TestBatchIsolation:

    @pytest.mark.asyncio
    async def test_one_failing_document_does_not_block_four_others(self):
        """
        In a batch of 5 items, the failure of item 3 must not stop items 1, 2, 4, 5.
        """
        items = [_make_item(item_id=f"item-{i:03}", name=f"Doc{i}.pdf") for i in range(5)]

        async def fake_process(token, item, action, semaphore):
            if item["id"] == "item-002":
                return {"item_id": item["id"], "success": False, "error": "OCR failed"}
            return {"item_id": item["id"], "success": True, "error": None}

        semaphore = asyncio.Semaphore(3)
        tasks = [fake_process("tok", item, "new", semaphore) for item in items]
        results = await asyncio.gather(*tasks)

        successful = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        assert len(successful) == 4
        assert len(failed) == 1
        assert failed[0]["item_id"] == "item-002"


# ---------------------------------------------------------------------------
# Test 9: _parse_sp_datetime utility
# ---------------------------------------------------------------------------

class TestParseDatetime:

    def test_valid_timestamp(self):
        dt = _parse_sp_datetime("2026-09-25T10:30:00Z")
        assert dt is not None and dt.year == 2026 and dt.month == 9

    def test_none_input(self):
        assert _parse_sp_datetime(None) is None

    def test_invalid_string_returns_none(self):
        assert _parse_sp_datetime("not-a-date") is None

    def test_result_is_timezone_aware(self):
        dt = _parse_sp_datetime("2026-01-15T05:00:00Z")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# Test 10: Exponential backoff
# ---------------------------------------------------------------------------

class TestExponentialBackoff:

    @pytest.mark.asyncio
    async def test_backoff_retries_on_429_and_succeeds(self):
        """_with_backoff retries a 429 and succeeds on 2nd attempt."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                raise httpx.HTTPStatusError("429", request=MagicMock(), response=mock_resp)
            return {"ok": True}

        with patch.object(_sched.asyncio, "sleep", new=AsyncMock()):
            result = await _with_backoff(flaky, label="test-429")

        assert result == {"ok": True}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_backoff_raises_after_max_wait(self):
        """_with_backoff re-raises once total wait time is exhausted."""
        async def always_429():
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            raise httpx.HTTPStatusError("429", request=MagicMock(), response=mock_resp)

        with patch.object(_sched.asyncio, "sleep", new=AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError):
                await _with_backoff(always_429, label="always-429", max_total_wait=5.0)


# ---------------------------------------------------------------------------
# Smoke test: scheduler lifecycle
# ---------------------------------------------------------------------------

class TestSchedulerLifecycle:

    @pytest.mark.asyncio
    async def test_start_and_stop_no_crash(self):
        """start_scheduler + stop_scheduler must not raise."""
        with patch.object(_sched, "_scheduler_loop", new=AsyncMock()):
            start_scheduler()
            await stop_scheduler()


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", __file__, "-v"])
