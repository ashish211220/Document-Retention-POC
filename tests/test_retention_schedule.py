from datetime import date
from app.services.retention_schedule_service import calculate_retention_schedule


# ── FE+N / AL+N / CD+N — Year Addition Rules ─────────────────────────────────

def test_fe4_active():
    """Standard active case: FE+4, not yet expired."""
    eval_date = date(2026, 9, 16)
    schedule = calculate_retention_schedule("FE+4", "2026-01-15", eval_date)
    assert schedule.retention_start_date == "2026-01-15"
    assert schedule.retention_end_date == "2030-01-15"
    assert schedule.retention_period == "4 Years"
    assert schedule.lifecycle_status == "Active"
    assert schedule.review_required is False
    assert schedule.review_reason is None
    assert "Retain until 2030-01-15" in schedule.next_action


def test_fe4_eligible_for_review():
    """Expired retention: FE+4 past end date → triggers human review."""
    eval_date = date(2026, 9, 16)
    schedule = calculate_retention_schedule("FE+4", "2020-01-15", eval_date)
    assert schedule.retention_end_date == "2024-01-15"
    assert schedule.lifecycle_status == "Eligible for Review"
    assert schedule.review_required is True
    assert schedule.review_reason is not None
    assert "Human Review Required" in schedule.next_action


def test_al_with_years_active():
    """AL+2: event-based rule with +N suffix — date-calculable."""
    eval_date = date(2026, 9, 16)
    schedule = calculate_retention_schedule("AL+2", "2025-01-01", eval_date)
    assert schedule.retention_end_date == "2027-01-01"
    assert schedule.lifecycle_status == "Active"
    assert schedule.review_required is False


def test_cd_with_years():
    """CD+1: remove after creation date + 1 year."""
    eval_date = date(2026, 9, 16)
    schedule = calculate_retention_schedule("CD+1", "2024-01-01", eval_date)
    assert schedule.retention_end_date == "2025-01-01"
    assert schedule.lifecycle_status == "Eligible for Review"
    assert schedule.review_required is True


# ── Permanent Retention ───────────────────────────────────────────────────────

def test_permanent_retention():
    """Permanent: never deletion-eligible, review_required always False."""
    schedule = calculate_retention_schedule("Permanent", "2026-01-15")
    assert schedule.retention_end_date is None
    assert schedule.retention_period == "Permanent"
    assert schedule.lifecycle_status == "Permanent Retention"
    assert schedule.review_required is False
    assert schedule.review_reason is None
    assert schedule.next_action == "Retain Permanently"


def test_permanent_uppercase_lowercase():
    """Permanent rule is case-insensitive."""
    s1 = calculate_retention_schedule("PERMANENT", "2025-01-01")
    s2 = calculate_retention_schedule("permanent", "2025-01-01")
    assert s1.lifecycle_status == "Permanent Retention"
    assert s2.lifecycle_status == "Permanent Retention"


# ── Month Rules ───────────────────────────────────────────────────────────────

def test_6_months_expired():
    """6-month rule: past end date triggers review."""
    eval_date = date(2026, 9, 16)
    schedule = calculate_retention_schedule("6 months", "2026-01-15", eval_date)
    assert schedule.retention_end_date == "2026-07-15"
    assert schedule.lifecycle_status == "Eligible for Review"
    assert schedule.review_required is True


def test_6_months_active():
    """6-month rule: not yet expired."""
    eval_date = date(2026, 9, 16)
    schedule = calculate_retention_schedule("6 months", "2026-06-01", eval_date)
    assert schedule.lifecycle_status == "Active"
    assert schedule.review_required is False


# ── YE vs FE Distinction ──────────────────────────────────────────────────────

def test_ye_uses_calendar_year_end():
    """YE standalone uses December 31."""
    eval_date = date(2027, 1, 1)
    schedule = calculate_retention_schedule("YE", "2026-01-15", eval_date)
    assert schedule.retention_end_date == "2026-12-31"
    assert schedule.lifecycle_status == "Eligible for Review"
    assert schedule.review_required is True


def test_fe_uses_fiscal_year_end():
    """FE standalone uses June 30 (configured fiscal year-end)."""
    eval_date = date(2027, 1, 1)
    schedule = calculate_retention_schedule("FE", "2026-01-15", eval_date)
    assert schedule.retention_end_date == "2026-06-30"
    assert schedule.lifecycle_status == "Eligible for Review"
    assert schedule.review_required is True


def test_fe_different_from_ye():
    """FE and YE must produce different end dates."""
    schedule_ye = calculate_retention_schedule("YE", "2026-01-15", date(2025, 1, 1))
    schedule_fe = calculate_retention_schedule("FE", "2026-01-15", date(2025, 1, 1))
    assert schedule_ye.retention_end_date != schedule_fe.retention_end_date


# ── Standalone Rules That MUST Require Human Review ──────────────────────────

def test_av_standalone_requires_review():
    """AV without +N MUST always require human review (manual determination)."""
    schedule = calculate_retention_schedule("AV", "2026-01-15")
    assert schedule.review_required is True
    assert schedule.review_reason is not None
    assert "AV" in schedule.review_reason
    assert "Human Review Required" in schedule.next_action


def test_al_standalone_requires_review():
    """AL without +N MUST require review — event end date is unknown."""
    schedule = calculate_retention_schedule("AL", "2026-01-15")
    assert schedule.review_required is True
    assert schedule.review_reason is not None
    assert "AL" in schedule.review_reason
    assert "Human Review Required" in schedule.next_action


def test_us_standalone_requires_review():
    """US (delete once superseded) MUST require review — supersession status unknown."""
    schedule = calculate_retention_schedule("US", "2026-01-15")
    assert schedule.review_required is True
    assert schedule.review_reason is not None
    assert "US" in schedule.review_reason
    assert "Human Review Required" in schedule.next_action


def test_ad_standalone_requires_review():
    """AD (delete once asset disposed) MUST require review — disposal date unknown."""
    schedule = calculate_retention_schedule("AD", "2026-01-15")
    assert schedule.review_required is True
    assert schedule.review_reason is not None
    assert "AD" in schedule.review_reason
    assert "Human Review Required" in schedule.next_action


def test_uo_standalone_requires_review():
    """UO (unopened/last modified) MUST require review — last-modified date unknown."""
    schedule = calculate_retention_schedule("UO", "2026-01-15")
    assert schedule.review_required is True
    assert schedule.review_reason is not None
    assert "UO" in schedule.review_reason
    assert "Human Review Required" in schedule.next_action


# ── LA (Life of Asset) ────────────────────────────────────────────────────────

def test_la_standalone_active_no_review():
    """LA standalone does NOT require immediate review."""
    schedule = calculate_retention_schedule("LA", "2026-01-15")
    assert schedule.lifecycle_status == "Active"
    assert schedule.review_required is False
    assert schedule.retention_period == "Life of Asset"


# ── Missing / Invalid Rules ───────────────────────────────────────────────────

def test_no_rule_requires_review():
    """Missing retention rule → Indeterminate + review required."""
    schedule = calculate_retention_schedule(None, "2026-01-15")
    assert schedule.lifecycle_status == "Indeterminate"
    assert schedule.review_required is True
    assert schedule.review_reason is not None


def test_unknown_rule_requires_review():
    """Unknown rule code → Indeterminate + review required."""
    schedule = calculate_retention_schedule("ZZZUNKNOWN", "2026-01-15")
    assert schedule.lifecycle_status == "Indeterminate"
    assert schedule.review_required is True
    assert schedule.review_reason is not None


def test_fallback_when_date_missing():
    """No document date → uses today's date as start date."""
    schedule = calculate_retention_schedule("FE+3", None)
    assert schedule.retention_start_date == date.today().isoformat()


def test_various_date_formats():
    """Varied date string formats are parsed correctly."""
    s1 = calculate_retention_schedule("AL+2", "January 15, 2026")
    assert s1.retention_start_date == "2026-01-15"
    assert s1.retention_end_date == "2028-01-15"

    s2 = calculate_retention_schedule("AL+5", "2025/05/20")
    assert s2.retention_start_date == "2025-05-20"
    assert s2.retention_end_date == "2030-05-20"


# ── CE (alias for YE) ─────────────────────────────────────────────────────────

def test_ce_alias_for_ye():
    """CE is treated as an alias for YE (calendar year-end)."""
    eval_date = date(2027, 1, 1)
    schedule = calculate_retention_schedule("CE", "2026-01-15", eval_date)
    assert schedule.retention_end_date == "2026-12-31"
    assert schedule.lifecycle_status == "Eligible for Review"


if __name__ == "__main__":
    import sys
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {t.__name__}: {e}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"Passed: {passed}  Failed: {failed}  Total: {passed + failed}")
    sys.exit(0 if failed == 0 else 1)
