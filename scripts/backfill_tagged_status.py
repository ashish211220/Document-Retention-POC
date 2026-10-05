"""
Backfill script: set document_tagged_status and deletion_status on existing
ClassificationRecord rows that pre-date the d5e6f7a8b9c0 migration.

Usage (run once, manually — NOT run by default):
    python scripts/backfill_tagged_status.py

Who should run this:
    Run AFTER applying the d5e6f7a8b9c0 Alembic migration if you have
    existing records you want to assign initial SP-facing status values.

Safety:
    - Read-only dry-run by default (set DRY_RUN=false to commit changes).
    - Only updates rows where document_tagged_status IS NULL.
    - Does NOT touch SharePoint or re-trigger any sync.
    - Does NOT re-evaluate confidence scores. Uses existing DB values only.

Mapping logic:
    ClassificationRecord.status   →  document_tagged_status
    'auto_approved'               →  'Auto-Tagged'
    anything else                 →  'Review Pending'

    deletion_status (default 'Not Deleted' for all backfilled rows)

NOTE: Records already holding 'Reviewed' or 'Manually Tagged' in SharePoint
      must be updated separately via the dashboard read-back flow, not here.
      This script only sets the backend-side DB column as a starting baseline.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.db.database import async_session
from app.models.db_models import ClassificationRecord
from app.models.sharepoint_enums import DocumentTaggedStatus, DeletionStatus
from app.utils.logger import get_logger

logger = get_logger("backfill_tagged_status")

# Set to False to actually commit changes. Default is safe/dry-run mode.
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"


async def backfill():
    logger.info(f"=== Backfill document_tagged_status / deletion_status ({'DRY RUN' if DRY_RUN else 'LIVE'}) ===")

    async with async_session() as db:
        # Only process rows that have never had document_tagged_status set
        stmt = select(ClassificationRecord).where(
            ClassificationRecord.document_tagged_status.is_(None)
        )
        result = await db.execute(stmt)
        records = result.scalars().all()

        logger.info(f"Found {len(records)} records with NULL document_tagged_status.")

        auto_tagged = 0
        review_pending = 0

        for rec in records:
            if rec.status == "auto_approved":
                tagged = DocumentTaggedStatus.AUTO_TAGGED.value
                auto_tagged += 1
            else:
                tagged = DocumentTaggedStatus.REVIEW_PENDING.value
                review_pending += 1

            deletion = DeletionStatus.NOT_DELETED.value

            logger.info(
                f"  {rec.id[:8]}... status={rec.status!r} "
                f"→ document_tagged_status={tagged!r}, deletion_status={deletion!r}"
            )

            if not DRY_RUN:
                rec.document_tagged_status = tagged
                rec.deletion_status = deletion

        if DRY_RUN:
            logger.info("DRY RUN complete — no changes written. Set DRY_RUN=false to commit.")
        else:
            await db.commit()
            logger.info(
                f"Backfill committed. "
                f"Auto-Tagged: {auto_tagged}, Review Pending: {review_pending}."
            )


if __name__ == "__main__":
    asyncio.run(backfill())
