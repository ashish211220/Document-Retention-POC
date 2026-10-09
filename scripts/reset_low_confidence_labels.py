"""
scripts/reset_low_confidence_labels.py

Finds all ClassificationRecord rows where:
  - purview_label_applied = True
  - confidence_score < AUTO_TAG_CONFIDENCE_THRESHOLD (default 0.70)

Resets them to:
  - purview_label_applied = False
  - purview_label_applied_at = None
  - status = 'review_recommended' (if not already pending_review)

Run dry-run first (default), then pass --apply to commit changes.

Usage:
    python scripts/reset_low_confidence_labels.py          # dry-run
    python scripts/reset_low_confidence_labels.py --apply  # commits to DB
"""

import asyncio
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(override=True)

from sqlalchemy import select
from app.db.database import async_session
from app.models.db_models import ClassificationRecord
from app.config import AUTO_TAG_CONFIDENCE_THRESHOLD

THRESHOLD = AUTO_TAG_CONFIDENCE_THRESHOLD / 100.0  # convert from 70 -> 0.70


async def run(apply: bool):
    print(f"\n{'='*60}")
    print(f"  Reset Low-Confidence Purview Labels")
    print(f"  Threshold : {AUTO_TAG_CONFIDENCE_THRESHOLD}% (decimal: {THRESHOLD})")
    print(f"  Mode      : {'APPLY — changes will be committed' if apply else 'DRY RUN — no changes made'}")
    print(f"{'='*60}\n")

    async with async_session() as db:
        stmt = select(ClassificationRecord).where(
            ClassificationRecord.purview_label_applied == True,
            ClassificationRecord.confidence_score < THRESHOLD,
        )
        result = await db.execute(stmt)
        records = result.scalars().all()

        if not records:
            print("  No affected records found. Everything looks correct!")
            return

        print(f"  Found {len(records)} affected records:\n")
        print(f"  {'ID':<38} {'Doc Type':<35} {'Score':>7}  {'Status'}")
        print(f"  {'-'*38} {'-'*35} {'-'*7}  {'-'*20}")

        for rec in records:
            score_pct = f"{rec.confidence_score * 100:.1f}%"
            doc_type = (rec.document_type or "Unknown")[:35]
            print(f"  {rec.id:<38} {doc_type:<35} {score_pct:>7}  {rec.status}")

            if apply:
                rec.purview_label_applied = False
                rec.purview_label_applied_at = None
                if rec.status == "auto_approved":
                    rec.status = "review_recommended"

        if apply:
            await db.commit()
            print(f"\n  DONE — {len(records)} records reset.")
            print("  The scheduler will re-evaluate these on the next poll cycle.")
            print("  NOTE: The Purview label already applied to the file in SharePoint")
            print("  is NOT automatically removed — that requires a separate Graph API call.")
        else:
            print(f"\n  DRY RUN complete — {len(records)} records would be reset.")
            print("  Re-run with --apply to commit these changes.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Commit changes to DB (default: dry-run)")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))
