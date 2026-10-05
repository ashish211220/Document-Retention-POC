"""Add auto-polling fields to classifications table

Revision ID: c4d5e6f7a8b9
Revises: b3e7f1a2c9d4
Create Date: 2026-09-27 11:00:00.000000

What this migration does
------------------------
Extends the `classifications` table with fields required by the automatic
background polling scheduler (Part 1 and Part 3 of the auto-sync feature):

  sharepoint_last_modified  (timestamp, nullable)
      Stores the Graph API `lastModifiedDateTime` of the file at the time we
      last processed it. Used by the skip-check: if the file's current
      lastModifiedDateTime equals this value, skip (unchanged). If newer,
      treat as reclassification.

  processing_status         (str, nullable, default 'pending')
      Lifecycle state for the scheduler:
        'pending'           — not yet attempted
        'processing'        — currently being worked on (in-progress lock)
        'completed'         — fully classified and synced
        'failed'            — last attempt failed (will retry up to MAX_PROCESSING_RETRIES)
        'failed_permanent'  — exceeded max retries; needs human attention
        'source_deleted'    — file was removed from SharePoint mid-processing

  processing_error          (text, nullable)
      Last error message for a failed document. Useful for dashboard display
      and debugging without having to dig through logs.

  processing_attempts       (int, default 0)
      Number of complete classification attempts made. Used to enforce
      MAX_PROCESSING_RETRIES before marking as failed_permanent.

No existing columns are renamed or removed. Safe to apply to a live database;
all new columns are nullable or have server-side defaults.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3e7f1a2c9d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply schema changes."""
    op.add_column(
        "classifications",
        sa.Column("sharepoint_last_modified", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "classifications",
        sa.Column("processing_status", sa.String(), nullable=True, server_default="pending"),
    )
    op.add_column(
        "classifications",
        sa.Column("processing_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "classifications",
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    # Index for fast scheduler queries (find pending/failed items)
    op.create_index(
        "ix_classifications_processing_status",
        "classifications",
        ["processing_status"],
        unique=False,
    )
    op.create_index(
        "ix_classifications_sharepoint_item_id",
        "classifications",
        ["sharepoint_item_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert schema changes."""
    op.drop_index("ix_classifications_sharepoint_item_id", table_name="classifications")
    op.drop_index("ix_classifications_processing_status", table_name="classifications")
    op.drop_column("classifications", "processing_attempts")
    op.drop_column("classifications", "processing_error")
    op.drop_column("classifications", "processing_status")
    op.drop_column("classifications", "sharepoint_last_modified")
