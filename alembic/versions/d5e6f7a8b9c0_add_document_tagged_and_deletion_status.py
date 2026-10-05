"""Add document_tagged_status and deletion_status to classifications

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-28 16:00:00.000000

What this migration does
------------------------
Extends the `classifications` table with two new string columns that mirror
the SharePoint Choice column values in POC_Documents_Retention:

  document_tagged_status  (string, nullable)
      Mirrors the DocumentTagged SharePoint Choice column.
      Backend-owned values: 'Auto-Tagged' | 'Review Pending'
      Human-owned values (read-only from backend): 'Reviewed' | 'Manually Tagged'
      Null for records created before this migration (backfill separately with
      scripts/backfill_tagged_status.py if needed).

  deletion_status  (string, nullable, server_default 'Not Deleted')
      Mirrors the isDeleted SharePoint Choice column (which is now a 4-state
      choice even though the column name is still 'isDeleted').
      Backend writes 'Not Deleted' on initial creation only.
      Human-owned: 'Deletion Revised' | 'Deletion Approved' | 'Deleted'

IMPORTANT:
  - No existing columns are renamed or removed. Safe to apply on a live DB.
  - All new columns are nullable or have server-side defaults.
  - Do NOT reorder or rename existing enum values in sharepoint_sync_type_enum
    or sharepoint_sync_status_enum — those are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply schema changes."""
    op.add_column(
        "classifications",
        sa.Column(
            "document_tagged_status",
            sa.String(),
            nullable=True,
            comment=(
                "Mirrors DocumentTagged SP Choice column. "
                "Backend values: 'Auto-Tagged', 'Review Pending'. "
                "Human values (read-only from backend): 'Reviewed', 'Manually Tagged'."
            ),
        ),
    )
    op.add_column(
        "classifications",
        sa.Column(
            "deletion_status",
            sa.String(),
            nullable=True,
            server_default="Not Deleted",
            comment=(
                "Mirrors isDeleted SP Choice column (4-state despite the boolean name). "
                "Backend writes 'Not Deleted' on creation only. "
                "Human-owned: 'Deletion Revised', 'Deletion Approved', 'Deleted'."
            ),
        ),
    )


def downgrade() -> None:
    """Revert schema changes."""
    op.drop_column("classifications", "deletion_status")
    op.drop_column("classifications", "document_tagged_status")
