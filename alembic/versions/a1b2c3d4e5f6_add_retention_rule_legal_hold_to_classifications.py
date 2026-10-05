"""Add retention_rule and legal_hold to classifications

Revision ID: a1b2c3d4e5f6
Revises: 68e81369ea6d
Create Date: 2026-09-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '68e81369ea6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add retention_rule and legal_hold columns to classifications table."""
    op.add_column('classifications',
        sa.Column('retention_rule', sa.String(), nullable=True)
    )
    op.add_column('classifications',
        sa.Column('legal_hold', sa.Boolean(), nullable=True, server_default=sa.false())
    )


def downgrade() -> None:
    """Remove retention_rule and legal_hold columns from classifications table."""
    op.drop_column('classifications', 'legal_hold')
    op.drop_column('classifications', 'retention_rule')
