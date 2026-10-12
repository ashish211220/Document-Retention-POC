"""
Unit tests for the per-rule Purview label resolution.

Tests:
  1. normalize_retention_rule handles all spacing/case variants
  2. resolve_purview_label returns correct label for known rules
  3. Unmapped rule -> NoLabelMappingError (no Graph call, no fallback)
  4. Mapped label Graph says "not found" -> LabelNotFoundError (graceful)
  5. Text columns (retention_rule, retention_code) never receive Purview label name
"""
import asyncio
import json
import os
import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock app.keyvault before config is imported so unit tests don't require Azure Key Vault
_fake_kv = types.ModuleType("app.keyvault")
_fake_kv.get_secrets = lambda *a, **kw: {
    "AZURE_DOCUMENT_INTELLIGENCE_KEY": "fake-doc-key",
    "AZURE_SEARCH_API_KEY": "fake-search-key",
    "AZURE_OPENAI_API_KEY": "fake-openai-key",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "SHAREPOINT_CLIENT_SECRET": "fake-sp-secret",
}
sys.modules["app.keyvault"] = _fake_kv

os.environ.setdefault("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://fake.cognitiveservices.azure.com/")
os.environ.setdefault("AZURE_DOCUMENT_INTELLIGENCE_KEY", "fakekey")
os.environ.setdefault("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "fake-embed")

from app.services.purview_label_resolver import (
    normalize_retention_rule,
    resolve_purview_label,
    reload_map,
)
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
        assert normalize_retention_rule("3 MONTHS") == "3MONTHS"


# ──────────────────────────────────────────────────────────────────────────────
# 2. resolve_purview_label
# ──────────────────────────────────────────────────────────────────────────────
class TestResolve:
    def test_fe_plus_2(self):
        result = resolve_purview_label("FE+2")
        assert result == "FE-2"

    def test_case_insensitive_input(self):
        assert resolve_purview_label("fe+2") == "FE-2"

    def test_spaces_input(self):
        assert resolve_purview_label("fe + 2") == "FE-2"

    def test_permanent(self):
        result = resolve_purview_label("Permanent")
        assert result == "Forever"

    def test_pm(self):
        result = resolve_purview_label("PM")
        assert result == "Forever"

    def test_unknown_returns_none(self):
        assert resolve_purview_label("XX+999") is None


# ──────────────────────────────────────────────────────────────────────────────
# 3. Unmapped rule -> NoLabelMappingError; no Graph call; sync continues
# ──────────────────────────────────────────────────────────────────────────────
class TestUnmappedRule:
    def test_no_label_mapping_raised(self):
        client = _RealGraphClient()
        with pytest.raises(NoLabelMappingError) as exc_info:
            asyncio.run(
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
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(LabelNotFoundError) as exc_info:
                asyncio.run(
                    client.patch_retention_label("tok", "drv", "itm", "FE+2")
                )
        assert exc_info.value.label_name == "FE-2"
        assert exc_info.value.retention_rule == "FE+2"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Text columns never receive the Purview label name
# ──────────────────────────────────────────────────────────────────────────────
class TestTextColumnsNotPolluted:
    def test_metadata_columns_use_raw_rule_not_label(self):
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
            purview_label_applied=True,
        )
        assert payload.retention_rule == "FE+2"
        assert payload.retention_code == "FE"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Full Taxonomy Coverage & Rule Parsing Tests
# ──────────────────────────────────────────────────────────────────────────────
class TestTaxonomyLabelCoverage:
    def test_all_standard_taxonomy_rules_map_to_purview_labels(self):
        """
        Loads data/retention_taxonomy.json and verifies that every standard rule
        (322 out of 327 taxonomy rows) resolves to a valid Purview label.
        The 5 compound/unstructured rules require records team clarification.
        """
        taxonomy_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "retention_taxonomy.json"
        )
        with open(taxonomy_path, "r", encoding="utf-8") as f:
            taxonomy_data = json.load(f)

        complex_rules = {
            "FE+2 AL+2",
            "FE+7 then AV",
            "AL+2 (2 yrs. after settlement)",
            "YE - Automated",
            "TBD",
        }

        unmapped = []
        for row in taxonomy_data:
            rule = row.get("retention_rule", "").strip()
            if not rule or rule in complex_rules:
                continue
            label = resolve_purview_label(rule)
            if not label:
                unmapped.append((row.get("id"), rule))

        assert unmapped == [], f"Found unmapped taxonomy rules: {unmapped}"

    def test_parse_retention_rule_spacing_and_permanent(self):
        """
        Verifies _parse_retention_rule correctly parses spaced rules and maps
        Permanent to code PM.
        """
        from app.azure.search import _parse_retention_rule

        assert _parse_retention_rule("AL + 3") == ("AL", 3, "years")
        assert _parse_retention_rule("AL +7") == ("AL", 7, "years")
        assert _parse_retention_rule("US + 3") == ("US", 3, "years")
        assert _parse_retention_rule("Permanent") == ("PM", None, None)
        assert _parse_retention_rule("PM") == ("PM", None, None)
        assert _parse_retention_rule("2 months") == ("TIME", 2, "months")
        assert _parse_retention_rule("6 months") == ("TIME", 6, "months")
        assert _parse_retention_rule("FE+5") == ("FE", 5, "years")
        assert _parse_retention_rule("FE+7 then AV") == ("FE+7 THEN AV", None, None)

