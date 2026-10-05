"""Add SharePoint sync columns to classifications and create sharepoint_sync_logs table

Revision ID: b3e7f1a2c9d4
Revises: 0a79028db1ac
Create Date: 2026-09-25 15:30:00.000000

What this migration does
------------------------
A. Extends the existing `classifications` table with SharePoint tracking columns:
   - sharepoint_item_id          (str)  -- Graph API driveItem ID in POC_Source_Documents
   - sharepoint_list_item_id     (str)  -- List item ID in POC_Documents_Retention
   - purview_label_applied       (bool) -- True after PATCH /retentionLabel succeeds
   - purview_label_applied_at    (ts)   -- Timestamp of the successful Purview label call
   - sharepoint_synced_at        (ts)   -- Last fully-successful sync timestamp
   - sharepoint_drive_id         (str)  -- Drive ID of POC_Source_Documents (cached)
   - sharepoint_web_url          (str)  -- webUrl of file, stored for Document_Location

B. Creates `sharepoint_sync_logs` table -- operational log of each individual
   Graph API write attempt (independent per-step retry tracking).

No existing columns are renamed or removed. This migration is safe to apply
to a live database; all new columns are nullable or have server-side defaults.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3e7f1a2c9d4"
down_revision: Union[str, Sequence[str], None] = "0a79028db1ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum type helpers (needed for PostgreSQL; SQLite ignores them)
sync_type_enum = sa.Enum(
    "metadata_columns",
    "retention_label",
    "retention_list_item",
    "audit_log_entry",
    name="sharepoint_sync_type_enum",
)
sync_status_enum = sa.Enum(
    "success",
    "failed",
    "retrying",
    name="sharepoint_sync_status_enum",
)


def upgrade() -> None:
    """Apply schema changes."""

    # A. Extend classifications table
    op.add_column("classifications", sa.Column("sharepoint_item_id", sa.String(), nullable=True))
    op.add_column("classifications", sa.Column("sharepoint_list_item_id", sa.String(), nullable=True))
    op.add_column("classifications", sa.Column(
        "purview_label_applied", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.add_column("classifications", sa.Column("purview_label_applied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("classifications", sa.Column("sharepoint_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("classifications", sa.Column("sharepoint_drive_id", sa.String(), nullable=True))
    op.add_column("classifications", sa.Column("sharepoint_web_url", sa.String(), nullable=True))

    # B. Create enum types with raw SQL (IF NOT EXISTS avoids duplicate errors in any context)
    op.execute("DO $$ BEGIN "
               "CREATE TYPE sharepoint_sync_type_enum AS ENUM "
               "('metadata_columns','retention_label','retention_list_item','audit_log_entry'); "
               "EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN "
               "CREATE TYPE sharepoint_sync_status_enum AS ENUM ('success','failed','retrying'); "
               "EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # C. Create sharepoint_sync_logs table
    op.create_table(
        "sharepoint_sync_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("classification_record_id", sa.String(), nullable=False),
        sa.Column("sync_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["classification_record_id"],
            ["classifications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sharepoint_sync_logs_classification_record_id",
        "sharepoint_sync_logs",
        ["classification_record_id"],
        unique=False,
    )
    op.create_index(
        "ix_sharepoint_sync_logs_status",
        "sharepoint_sync_logs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Revert schema changes."""
    op.drop_index("ix_sharepoint_sync_logs_status", table_name="sharepoint_sync_logs")
    op.drop_index("ix_sharepoint_sync_logs_classification_record_id", table_name="sharepoint_sync_logs")
    op.drop_table("sharepoint_sync_logs")

    sync_type_enum.drop(op.get_bind(), checkfirst=True)
    sync_status_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_column("classifications", "sharepoint_web_url")
    op.drop_column("classifications", "sharepoint_drive_id")
    op.drop_column("classifications", "sharepoint_synced_at")
    op.drop_column("classifications", "purview_label_applied_at")
    op.drop_column("classifications", "purview_label_applied")
    op.drop_column("classifications", "sharepoint_list_item_id")
    op.drop_column("classifications", "sharepoint_item_id")

