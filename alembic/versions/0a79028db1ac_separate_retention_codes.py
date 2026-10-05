"""separate_retention_codes

Revision ID: 0a79028db1ac
Revises: a1b2c3d4e5f6
Create Date: 2026-09-24 12:37:01.550352

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a79028db1ac'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('classifications', sa.Column('retention_code', sa.String(), nullable=True))
    op.add_column('classifications', sa.Column('retention_period_unit', sa.String(), nullable=True))
    # We leave retention_period as String in the DB but store integer values (or strings) in it to avoid SQLite/Postgres ALTER TABLE issues during POC testing.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('classifications', 'retention_period_unit')
    op.drop_column('classifications', 'retention_code')
