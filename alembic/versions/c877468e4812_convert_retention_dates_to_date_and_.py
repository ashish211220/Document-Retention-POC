"""convert_retention_dates_to_date_and_period_to_integer

Revision ID: c877468e4812
Revises: f83318ddeec6
Create Date: 2026-10-12 17:20:25.762980

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c877468e4812'
down_revision: Union[str, Sequence[str], None] = 'f83318ddeec6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            'classifications',
            'retention_start_date',
            existing_type=sa.VARCHAR(),
            type_=sa.Date(),
            existing_nullable=True,
            postgresql_using="NULLIF(retention_start_date, '')::date",
        )
        op.alter_column(
            'classifications',
            'retention_end_date',
            existing_type=sa.VARCHAR(),
            type_=sa.Date(),
            existing_nullable=True,
            postgresql_using="NULLIF(retention_end_date, '')::date",
        )
        op.alter_column(
            'classifications',
            'retention_period',
            existing_type=sa.VARCHAR(),
            type_=sa.Integer(),
            existing_nullable=True,
            postgresql_using="CASE WHEN retention_period ~ '^[0-9]+$' THEN retention_period::integer ELSE NULL END",
        )
    else:
        with op.batch_alter_table('classifications') as batch_op:
            batch_op.alter_column(
                'retention_start_date',
                existing_type=sa.VARCHAR(),
                type_=sa.Date(),
                existing_nullable=True,
            )
            batch_op.alter_column(
                'retention_end_date',
                existing_type=sa.VARCHAR(),
                type_=sa.Date(),
                existing_nullable=True,
            )
            batch_op.alter_column(
                'retention_period',
                existing_type=sa.VARCHAR(),
                type_=sa.Integer(),
                existing_nullable=True,
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            'classifications',
            'retention_period',
            existing_type=sa.Integer(),
            type_=sa.VARCHAR(),
            existing_nullable=True,
            postgresql_using="retention_period::text",
        )
        op.alter_column(
            'classifications',
            'retention_end_date',
            existing_type=sa.Date(),
            type_=sa.VARCHAR(),
            existing_nullable=True,
            postgresql_using="retention_end_date::text",
        )
        op.alter_column(
            'classifications',
            'retention_start_date',
            existing_type=sa.Date(),
            type_=sa.VARCHAR(),
            existing_nullable=True,
            postgresql_using="retention_start_date::text",
        )
    else:
        with op.batch_alter_table('classifications') as batch_op:
            batch_op.alter_column(
                'retention_period',
                existing_type=sa.Integer(),
                type_=sa.VARCHAR(),
                existing_nullable=True,
            )
            batch_op.alter_column(
                'retention_end_date',
                existing_type=sa.Date(),
                type_=sa.VARCHAR(),
                existing_nullable=True,
            )
            batch_op.alter_column(
                'retention_start_date',
                existing_type=sa.Date(),
                type_=sa.VARCHAR(),
                existing_nullable=True,
            )
