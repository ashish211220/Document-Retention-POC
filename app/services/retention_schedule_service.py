"""
Retention Schedule Service.

Implements records retention lifecycle governance:
  1. Parses retention rules from the approved taxonomy (e.g., 'FE+4', 'AL+2', 'Permanent', '6 months').
  2. Resolves retention_start_date from document date (with fallback to current date).
  3. Calculates retention_end_date.
  4. Determines lifecycle status ('Active' vs 'Eligible for Review' vs 'Permanent Retention').
  5. Sets human-in-the-loop review triggers (review_required: bool, next_action: str).

CRITICAL GOVERNANCE RULE:
  The system NEVER automatically deletes documents. Once a document's retention
  period has elapsed, it transitions to 'Eligible for Review', setting
  review_required=True to require explicit human authorization before disposal.
"""
from datetime import date, datetime
import re
from typing import Optional, Tuple

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Fiscal Year-End Configuration ────────────────────────────────────────────
# Adjust these if the organisation has a different fiscal year-end.
FISCAL_YEAR_END_MONTH = 6   # June
FISCAL_YEAR_END_DAY   = 30  # 30th


class RetentionSchedule(BaseModel):
    retention_start_date: str
    retention_end_date: Optional[str] = None
    retention_period: str
    lifecycle_status: str  # 'Active' | 'Eligible for Review' | 'Permanent Retention' | 'Indeterminate'
    review_required: bool
    review_reason: Optional[str] = None   # Explicit reason for human review
    next_action: str
    trigger_condition: Optional[str] = None  # Official definition from Exhibit A Legend


# ── Exhibit A: Official Legend Definitions ───────────────────────────────────
LEGEND_DEFINITIONS = {
    "AL": "Remove files after related Investment/item/event has liquidated, terminated, completed, expired, settled, or concluded",
    "AV": "Administratively Valuable (Responsible team manually determines retention)",
    "YE": "Remove after year end (calendar)",
    "FE": "Remove after year end (fiscal)",
    "PERMANENT": "These documents must be kept in perpetuity by law or policy",
    "CD": "Remove files after created",
    "UO": "Unopened (relates to last date modified for electronic records)",
    "US": "Delete once superseded (only keep most recent version)",
    "AD": "Delete once asset is disposed",
    "LA": "Life of Asset",
    "CE": "Remove after calendar year end (alias for YE)",
    "AC": "After Conclusion/Publication of Event (e.g. final audit findings)",
}


def _parse_start_date(raw_date: Optional[str]) -> date:
    """
    Parse document date string into a date object.
    Supports ISO formats (YYYY-MM-DD), slash formats (YYYY/MM/DD, MM/DD/YYYY),
    and common text representations. Fallbacks to today's date if missing or unparseable.
    """
    if not raw_date:
        return date.today()

    raw_date = str(raw_date).strip()
    
    date_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for fmt in date_formats:
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            continue

    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw_date)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    logger.warning(f"Could not parse document date '{raw_date}'. Defaulting to today's date.")
    return date.today()


def _add_years(start: date, years: int) -> date:
    """Add years to a date, handling leap-year Feb 29 safely."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(month=2, day=28, year=start.year + years)


def _add_months(start: date, months: int) -> date:
    """Add months to a date."""
    new_month = start.month + months
    new_year = start.year + (new_month - 1) // 12
    new_month = (new_month - 1) % 12 + 1
    day = min(start.day, 28)
    return date(new_year, new_month, day)


def _fiscal_year_end(reference: date) -> date:
    """
    Return the fiscal year-end date on or after the reference date.
    Uses FISCAL_YEAR_END_MONTH / FISCAL_YEAR_END_DAY configured above.
    """
    fy_end = date(reference.year, FISCAL_YEAR_END_MONTH, FISCAL_YEAR_END_DAY)
    if reference > fy_end:
        # Past this year's fiscal year-end — use next year's
        fy_end = date(reference.year + 1, FISCAL_YEAR_END_MONTH, FISCAL_YEAR_END_DAY)
    return fy_end


def calculate_retention_schedule(
    retention_rule: Optional[str],
    document_date: Optional[str] = None,
    evaluation_date: Optional[date] = None,
) -> RetentionSchedule:
    """
    Calculate the retention schedule and evaluate lifecycle status according to Exhibit A.

    Args:
        retention_rule: The rule string from taxonomy (e.g. 'FE+4', 'AL+2', 'Permanent', 'US+3', 'AV').
        document_date: Date extracted from document understanding (e.g. '2026-01-15').
        evaluation_date: Optional reference date to evaluate against (defaults to date.today()).

    Returns:
        RetentionSchedule with start date, end date, status, trigger condition, and human review flags.
    """
    current_date = evaluation_date or date.today()
    start_date = _parse_start_date(document_date)
    start_date_str = start_date.isoformat()

    if not retention_rule:
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Unspecified",
            lifecycle_status="Indeterminate",
            review_required=True,
            review_reason="No retention rule assigned. Manual assignment required.",
            next_action="Human Review Required: Assign retention rule",
            trigger_condition="No retention rule assigned.",
        )

    rule_norm = retention_rule.strip().upper()

    # ── Rule Type 1: Permanent ───────────────────────────────────────────────
    if "PERMANENT" in rule_norm:
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Permanent",
            lifecycle_status="Permanent Retention",
            review_required=False,
            review_reason=None,
            next_action="Retain Permanently",
            trigger_condition=LEGEND_DEFINITIONS["PERMANENT"],
        )

    # ── Rule Type 2: Relative Months (e.g. '2 MONTHS', '6 MONTHS') ───────────
    month_match = re.search(r"(\d+)\s*MONTHS?", rule_norm, re.IGNORECASE)
    if month_match:
        months = int(month_match.group(1))
        end_date = _add_months(start_date, months)
        end_date_str = end_date.isoformat()
        is_completed = current_date >= end_date
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=end_date_str,
            retention_period=f"{months} Month{'s' if months > 1 else ''}",
            lifecycle_status="Eligible for Review" if is_completed else "Active",
            review_required=is_completed,
            review_reason="Retention period has elapsed. Approve disposal or retain." if is_completed else None,
            next_action=(
                "Human Review Required: Approve Disposal or Retain"
                if is_completed
                else f"Retain until {end_date_str}"
            ),
            trigger_condition=f"Retain for {months} months from document date.",
        )

    # ── Rule Type 3: Standard Enterprise Year Addition (+X or -X) ──────────────────
    # Handles FE-4, AL-2, CE-1, YE-3, US-10, UO-5, AD-3, CD-1, AV-1, LA-3, etc.
    year_match = re.search(r"[+-](\d+)", rule_norm)
    if year_match:
        years = int(year_match.group(1))
        end_date = _add_years(start_date, years)
        end_date_str = end_date.isoformat()
        is_completed = current_date >= end_date

        # Determine trigger condition based on prefix
        if rule_norm.startswith("FE"):
            trigger = f"Remove {years} years after fiscal year end (FE). Note: start date is used as reference; for precise FE calculation, verify fiscal year-end."
        elif rule_norm.startswith("AL"):
            trigger = f"Remove {years} years after event/investment concludes or liquidates (AL)"
        elif rule_norm.startswith("AC"):
            trigger = f"Remove {years} years after conclusion/publication of event (AC)"
        elif rule_norm.startswith("US"):
            trigger = f"Retain for {years} years after being superseded by newer version (US)"
        elif rule_norm.startswith("UO"):
            trigger = f"Retain for {years} years after last unopened/modified date (UO)"
        elif rule_norm.startswith("AD"):
            trigger = f"Retain for {years} years after asset is disposed (AD)"
        elif rule_norm.startswith("LA"):
            trigger = f"Retain for life of asset + {years} years (LA)"
        elif rule_norm.startswith("CE") or rule_norm.startswith("YE"):
            trigger = f"Remove {years} years after calendar year end (YE/CE)"
        elif rule_norm.startswith("CD"):
            trigger = f"Remove {years} years after creation date (CD)"
        elif rule_norm.startswith("AV"):
            trigger = f"Retain {years} year{'s' if years > 1 else ''} after administrative value ends (AV)"
        else:
            trigger = f"Retain for {years} years after applicable event date."

        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=end_date_str,
            retention_period=f"{years} Year{'s' if years > 1 else ''}",
            lifecycle_status="Eligible for Review" if is_completed else "Active",
            review_required=is_completed,
            review_reason="Retention period has elapsed. Approve disposal or retain." if is_completed else None,
            next_action=(
                "Human Review Required: Approve Disposal or Retain"
                if is_completed
                else f"Retain until {end_date_str}"
            ),
            trigger_condition=trigger,
        )

    # ── Rule Type 4: Standalone Legend Codes (without +X) ────────────────────

    # US — Delete once superseded: review_required because supersession status is UNKNOWN
    if rule_norm == "US":
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Until Superseded",
            lifecycle_status="Active",
            review_required=True,
            review_reason="Rule US: Delete once superseded. Human must confirm whether a newer version exists.",
            next_action="Human Review Required: Confirm whether this document has been superseded by a newer version",
            trigger_condition=LEGEND_DEFINITIONS["US"],
        )

    # AV — Administratively Valuable: ALWAYS requires manual determination
    if "AV" in rule_norm or "ADMINISTRATIVE" in rule_norm:
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Until Administrative Value Ends",
            lifecycle_status="Active",
            review_required=True,
            review_reason="Rule AV: Responsible team must manually determine retention period and end date.",
            next_action="Human Review Required: Responsible team must manually determine retention period",
            trigger_condition=LEGEND_DEFINITIONS["AV"],
        )

    # AD — Delete once asset is disposed: review_required because disposal date is UNKNOWN
    if rule_norm == "AD":
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Until Asset Disposed",
            lifecycle_status="Active",
            review_required=True,
            review_reason="Rule AD: Delete once asset is disposed. Human must confirm asset disposal date and status.",
            next_action="Human Review Required: Confirm asset disposal date and approve retention decision",
            trigger_condition=LEGEND_DEFINITIONS["AD"],
        )

    # LA — Life of Asset: no disposal date known yet, flag for monitoring
    if rule_norm == "LA":
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Life of Asset",
            lifecycle_status="Active",
            review_required=False,
            review_reason=None,
            next_action="Retain for duration of asset lifecycle",
            trigger_condition=LEGEND_DEFINITIONS["LA"],
        )

    # AL — Remove after event liquidates/terminates: review_required because event date is UNKNOWN
    if rule_norm == "AL":
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Until Conclusion/Liquidation",
            lifecycle_status="Active",
            review_required=True,
            review_reason="Rule AL: Remove after related event liquidates/concludes. Human must confirm event completion date.",
            next_action="Human Review Required: Confirm event/investment conclusion or liquidation date",
            trigger_condition=LEGEND_DEFINITIONS["AL"],
        )

    # AC — After Conclusion/Publication: review_required because event date is UNKNOWN
    if rule_norm == "AC":
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Until Conclusion/Publication",
            lifecycle_status="Active",
            review_required=True,
            review_reason="Rule AC: Remove after event concludes or is published. Human must confirm completion date.",
            next_action="Human Review Required: Confirm event conclusion or publication date",
            trigger_condition=LEGEND_DEFINITIONS["AC"],
        )

    # UO — Unopened: last-modified date is unknown; flag for review
    if rule_norm == "UO":
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=None,
            retention_period="Until Last Unopened Date + Retention Period",
            lifecycle_status="Active",
            review_required=True,
            review_reason="Rule UO: Retention depends on last-modified/unopened date which cannot be automatically determined.",
            next_action="Human Review Required: Provide last modified date for UO retention calculation",
            trigger_condition=LEGEND_DEFINITIONS["UO"],
        )

    # FE — Fiscal Year End (no +N): use fiscal year-end
    if rule_norm == "FE":
        end_date = _fiscal_year_end(start_date)
        end_date_str = end_date.isoformat()
        is_completed = current_date >= end_date
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=end_date_str,
            retention_period="End of Fiscal Year",
            lifecycle_status="Eligible for Review" if is_completed else "Active",
            review_required=is_completed,
            review_reason="Retention period has elapsed (fiscal year-end). Approve disposal or retain." if is_completed else None,
            next_action=(
                "Human Review Required: Approve Disposal or Retain"
                if is_completed
                else f"Retain until fiscal year end ({end_date_str})"
            ),
            trigger_condition=LEGEND_DEFINITIONS["FE"],
        )

    # YE / CE — Calendar Year End (no +N): use Dec 31
    if rule_norm in ("YE", "CE"):
        end_date = date(start_date.year, 12, 31)
        end_date_str = end_date.isoformat()
        is_completed = current_date >= end_date
        return RetentionSchedule(
            retention_start_date=start_date_str,
            retention_end_date=end_date_str,
            retention_period="End of Calendar Year",
            lifecycle_status="Eligible for Review" if is_completed else "Active",
            review_required=is_completed,
            review_reason="Retention period has elapsed (calendar year-end). Approve disposal or retain." if is_completed else None,
            next_action=(
                "Human Review Required: Approve Disposal or Retain"
                if is_completed
                else f"Retain until calendar year end ({end_date_str})"
            ),
            trigger_condition=LEGEND_DEFINITIONS["YE"],
        )

    # ── Rule Type 5: TBD or Unspecified ──────────────────────────────────────
    return RetentionSchedule(
        retention_start_date=start_date_str,
        retention_end_date=None,
        retention_period=retention_rule,
        lifecycle_status="Indeterminate",
        review_required=True,
        review_reason=f"Unrecognized or unsupported retention rule: '{retention_rule}'. Manual policy interpretation required.",
        next_action="Human Review Required: Clarify retention period with compliance team",
        trigger_condition="Rule requires manual policy interpretation.",
    )
