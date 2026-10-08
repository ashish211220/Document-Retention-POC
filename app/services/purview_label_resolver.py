import json
import re
import os
from pathlib import Path
from typing import Optional

_LABELS_PATH = Path(__file__).parent.parent / "purview_labels.json"

def _load_per_rule_map() -> dict[str, str]:
    with open(_LABELS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # strip comment keys
    return {k: v for k, v in data.items() if not k.startswith("_")}

# Loaded once at import time
_PER_RULE_MAP: dict[str, str] = _load_per_rule_map()


def normalize_retention_rule(rule: str) -> str:
    # Uppercase, strip whitespace around + and around the whole string
    rule = (rule or "").strip().upper()
    rule = re.sub(r"\s*\+\s*", "+", rule)   # "FE + 2"  -> "FE+2"
    rule = re.sub(r"\s+", "", rule)          # "3 MONTHS" -> "3MONTHS" for lookup key
    return rule


def resolve_purview_label(retention_rule: str) -> Optional[str]:
    # Looks up the exact Purview label name for a given retention rule.
    normalized = normalize_retention_rule(retention_rule)
    return _PER_RULE_MAP.get(normalized)


def reload_map() -> None:
    # Reloads the label map from disk (useful in tests or after editing the JSON).
    global _PER_RULE_MAP
    _PER_RULE_MAP = _load_per_rule_map()
