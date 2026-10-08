"""
scripts/check_label_coverage.py

Reports which taxonomy rules have no Purview label mapping and vice-versa.
Does NOT modify any data.

Usage:
    python scripts/check_label_coverage.py
    python scripts/check_label_coverage.py --draft-json  # write draft purview_labels.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def normalize(rule: str) -> str:
    rule = (rule or "").strip().upper()
    rule = re.sub(r"\s*\+\s*", "+", rule)
    rule = re.sub(r"\s+", "", rule)
    return rule


def load_taxonomy_rules() -> list[str]:
    taxonomy_path = ROOT / "data" / "retention_taxonomy.json"
    with open(taxonomy_path, encoding="utf-8") as f:
        data = json.load(f)
    return [r for item in data if (r := item.get("retention_rule")) and r]


def load_label_map() -> dict[str, str]:
    map_path = ROOT / "app" / "purview_labels.json"
    with open(map_path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def is_complex(rule: str) -> bool:
    # Multi-rule or descriptive rules not in simple CODE or CODE+N form
    keywords = ["TBD", "THEN", "YRS", "SETTLEMENT", "AUTOMATED"]
    u = rule.upper()
    return any(k in u for k in keywords) or (" " in rule and "MONTHS" not in rule.upper())


def main():
    parser = argparse.ArgumentParser(description="Check Purview label coverage against taxonomy.")
    parser.add_argument("--draft-json", action="store_true", help="Write draft purview_labels.json additions")
    args = parser.parse_args()

    raw_rules = load_taxonomy_rules()
    label_map = load_label_map()
    mapped_keys = set(label_map.keys())

    print(f"\n{'='*60}")
    print(f"  Purview Label Coverage Report")
    print(f"{'='*60}")
    print(f"  Taxonomy rules found : {len(raw_rules)}")
    print(f"  Label map entries    : {len(mapped_keys)}")

    # --- A: Rules with NO label mapping ---
    no_mapping = []
    complex_rules = []
    for rule in sorted(set(raw_rules)):
        n = normalize(rule)
        if is_complex(rule):
            complex_rules.append(rule)
        elif n not in mapped_keys:
            no_mapping.append(rule)

    print(f"\n[A] Taxonomy rules with NO label in purview_labels.json ({len(no_mapping)}):")
    if no_mapping:
        for r in no_mapping:
            print(f"    ✗ '{r}'  (normalized: '{normalize(r)}')")
    else:
        print("    ✓ All rules are covered!")

    print(f"\n[B] Complex/multi-rules excluded from mapping ({len(complex_rules)}):")
    for r in complex_rules:
        print(f"    ⚠  '{r}'  — needs manual review")

    # --- B: Labels in map that match no taxonomy rule ---
    used_normalized = {normalize(r) for r in raw_rules if not is_complex(r)}
    unused_labels = [k for k in mapped_keys if k not in used_normalized]
    print(f"\n[C] Label map entries that match NO taxonomy rule ({len(unused_labels)}):")
    if unused_labels:
        for k in sorted(unused_labels):
            print(f"    ~ '{k}' -> '{label_map[k]}'")
    else:
        print("    ✓ Every label is referenced by at least one taxonomy rule.")

    # --- C: Near-misses ---
    print(f"\n[D] Near-miss analysis (same code+period, different format):")
    near_misses = []
    for rule in set(raw_rules):
        n = normalize(rule)
        if n != rule.strip().upper() and n in mapped_keys:
            near_misses.append((rule, n))
    if near_misses:
        for raw, norm in near_misses:
            print(f"    ⚠  Taxonomy has '{raw}' -> normalizes to '{norm}' (mapped OK)")
    else:
        print("    ✓ No near-misses found.")

    # --- D: Special patterns ---
    print(f"\n[E] Special pattern checks:")
    for rule in set(raw_rules):
        u = rule.upper()
        if "MONTHS" in u:
            print(f"    ℹ  Time/Other pattern: '{rule}' (normalized: '{normalize(rule)}')")
        if rule.upper() in ("PERMANENT", "PM"):
            print(f"    ℹ  Permanent: '{rule}'")

    # --- Optional: write draft additions ---
    if args.draft_json:
        draft = {}
        for rule in sorted(set(raw_rules)):
            if is_complex(rule):
                continue
            n = normalize(rule)
            if n not in mapped_keys:
                draft[n] = f"<PURVIEW_LABEL_NAME_FOR_{n}>"
        draft_path = ROOT / "app" / "purview_labels_draft_additions.json"
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(draft, f, indent=2)
        print(f"\n  Draft additions written to: {draft_path}")
        print("  Review and merge into app/purview_labels.json before using.")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
