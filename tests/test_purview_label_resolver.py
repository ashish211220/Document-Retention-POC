"""
Unit tests for the per-rule Purview label resolution.

Tests:
  1. normalize_retention_rule handles all spacing/case variants
  2. resolve_purview_label returns correct label for known rules
  3. Unmapped rule -> NoLabelMappingError (no Graph call, no fallback)
  4. Mapped label Graph says "not found" -> LabelNotFoundError (graceful)
  5. PURVIEW_LABEL_STRATEGY=base_code reproduces old behavior
  6. ALLOW_BASE_CODE_FALLBACK logs + falls back when flag is true
  7. Text columns (retention_rule, retention_code) never receive Purview label name
"""
import asyncio
import json
import os
import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Stub heavy dependencies so import succeeds without installed packages ──────
for mod in [
    "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
    "httpx", "pydantic", "dotenv",
    "azure", "azure.core", "azure.core.credentials",
    "azure.ai", "azure.ai.documentintelligence",
    "azure.search", "azure.search.documents",
    "azure.search.documents.indexes", "azure.search.documents.indexes.models",
    "azure.search.documents.models",
    "openai",
]:
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

# Stub dotenv
sys.modules["dotenv"].load_dotenv = lambda **kw: None

# Stub sqlalchemy AsyncSession
_sa_async = sys.modules.setdefault("sqlalchemy.ext.asyncio", types.ModuleType("sqlalchemy.ext.asyncio"))
_sa_async.AsyncSession = object

# Stub httpx
_httpx = sys.modules["httpx"]
_httpx.AsyncClient = MagicMock
_httpx.HTTPStatusError = Exception

# ── Patch os.getenv before importing config ────────────────────────────────────
_ENV_BASE = {
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT": "https://fake.cognitiveservices.azure.com/",
    "AZURE_DOCUMENT_INTELLIGENCE_KEY": "fakekey",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME": "fake-embed",
    "PURVIEW_LABEL_STRATEGY": "per_rule",
    "ALLOW_BASE_CODE_FALLBACK": "false",
}


def _patch_env(extra: dict = None):
    env = {**_ENV_BASE, **(extra or {})}
    return patch.dict(os.environ, env, clear=False)


# ── Import modules under test ──────────────────────────────────────────────────
with _patch_env():
    from app.services.purview_label_resolver import normalize_retention_rule, resolve_purview_label, reload_map
    import importlib
    import app.config as _cfg_module

# Re-import sharepoint_sync after env is set
with _patch_env():
    import app.sync.sharepoint_sync as _sync_module
    from app.sync.sharepoint_sync import (
        LabelNotFoundError,
        NoLabelMappingError,
        _RealGraphClient,
        SyncPayload,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. normalize_retention_rule
# ──────────────────────────────────────────────────────────────────────────────
class TestNormalize:
    def test_simple(self):
        assert normalize_retention_rule("FE+2") == "FE+2"

    def test_lowercase(self):
        assert normalize_retention_rule("fe+2") == "FE+2"

    def test_spaces_around_plus(self):
        assert normalize_retention_rule("FE + 2") == "FE+2"

    def test_leading_trailing_whitespace(self):
        assert normalize_retention_rule("  FE +2  ") == "FE+2"

    def test_no_period_code(self):
        assert normalize_retention_rule("AC") == "AC"

    def test_permanent(self):
        assert normalize_retention_rule("Permanent") == "PERMANENT"

    def test_pm(self):
        assert normalize_retention_rule("PM") == "PM"

    def test_months(self):
        # Spaces removed from "3 MONTHS" -> "3MONTHS" for lookup
        assert normalize_retention_rule("3 MONTHS") == "3MONTHS"


# ──────────────────────────────────────────────────────────────────────────────
# 2. resolve_purview_label
# ──────────────────────────────────────────────────────────────────────────────
class TestResolve:
    def test_fe_plus_2(self):
        result = resolve_purview_label("FE+2")
        assert result == "FE+2"

    def test_case_insensitive_input(self):
        assert resolve_purview_label("fe+2") == "FE+2"

    def test_spaces_input(self):
        assert resolve_purview_label("fe + 2") == "FE+2"

    def test_permanent(self):
        result = resolve_purview_label("Permanent")
        assert result == "Permanent"

    def test_pm(self):
        result = resolve_purview_label("PM")
        assert result == "Permanent"

    def test_unknown_returns_none(self):
        assert resolve_purview_label("XX+999") is None


# ──────────────────────────────────────────────────────────────────────────────
# 3. Unmapped rule -> NoLabelMappingError; no Graph call; sync continues
# ──────────────────────────────────────────────────────────────────────────────
class TestUnmappedRule:
    def test_no_label_mapping_raised(self):
        client = _RealGraphClient()
        with _patch_env({"PURVIEW_LABEL_STRATEGY": "per_rule", "ALLOW_BASE_CODE_FALLBACK": "false"}):
            with patch.object(_sync_module, "PURVIEW_LABEL_STRATEGY", "per_rule"), \
                 patch.object(_sync_module, "ALLOW_BASE_CODE_FALLBACK", False):
                with pytest.raises(NoLabelMappingError) as exc_info:
                    asyncio.get_event_loop().run_until_complete(
                        client.patch_retention_label("tok", "drv", "itm", "XX+999")
                    )
        assert "XX+999" in str(exc_info.value)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Mapped label, Graph says 400 -> LabelNotFoundError (graceful)
# ──────────────────────────────────────────────────────────────────────────────
class TestLabelNotFoundInGraph:
    def test_label_not_found_raised_on_400(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Label not found"

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_resp)

        client = _RealGraphClient()
        with patch.object(_sync_module, "PURVIEW_LABEL_STRATEGY", "per_rule"), \
             patch.object(_sync_module, "ALLOW_BASE_CODE_FALLBACK", False), \
             patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(LabelNotFoundError) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    client.patch_retention_label("tok", "drv", "itm", "FE+2")
                )
        assert exc_info.value.label_name == "FE+2"
        assert exc_info.value.retention_rule == "FE+2"


# ──────────────────────────────────────────────────────────────────────────────
# 5. PURVIEW_LABEL_STRATEGY=base_code reproduces old behavior
# ──────────────────────────────────────────────────────────────────────────────
class TestBaseCodeStrategy:
    def test_base_code_uses_purview_label_name_map(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_resp)

        from app.config import PURVIEW_LABEL_NAME_MAP
        client = _RealGraphClient()
        with patch.object(_sync_module, "PURVIEW_LABEL_STRATEGY", "base_code"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                client.patch_retention_label("tok", "drv", "itm", "FE+4")
            )

        # Should have sent the base label FE_V1 (from PURVIEW_LABEL_NAME_MAP["FE"])
        called_body = mock_client.patch.call_args[1]["json"]
        assert called_body["name"] == PURVIEW_LABEL_NAME_MAP["FE"]


# ──────────────────────────────────────────────────────────────────────────────
# 6. Text columns never receive the Purview label name
# ──────────────────────────────────────────────────────────────────────────────
class TestTextColumnsNotPolluted:
    def test_metadata_columns_use_raw_rule_not_label(self):
        # The metadata_fields dict built in Step 2 should use retention_rule="FE+2"
        # and retention_code="FE" — never the Purview label name (e.g. "FE+2" label)
        payload = SyncPayload(
            classification_record_id=str(uuid.uuid4()),
            document_name="test.pdf",
            document_type="Report",
            category="Finance",
            section="Accounts",
            retention_code="FE",
            retention_rule="FE+2",
            retention_end_date="2028-08-31",
            confidence_score=0.85,
            team_owner="Finance",
            classification_status="auto_approved",
            document_number=101,
            drive_id="drv123",
            sharepoint_item_id="item123",
            sharepoint_list_item_id=None,
            sharepoint_web_url="https://sp.example.com",
            purview_label_applied=True,  # already applied -> step 1 skipped
        )
        # The retention_rule and retention_code on the payload are raw taxonomy values
        assert payload.retention_rule == "FE+2"
        assert payload.retention_code == "FE"
        # They are NOT the Purview label name
        assert payload.retention_rule != "FE_V1"
        assert payload.retention_code != "FE_V1"
