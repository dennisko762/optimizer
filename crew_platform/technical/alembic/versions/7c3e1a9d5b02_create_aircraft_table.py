"""create aircraft table

Creates the ``aircraft`` table — the persistent, registration-keyed
aircraft record for the TechLog domain (model:
``crew_platform.technical.models.Aircraft``).

Revision ID: 7c3e1a9d5b02
Revises: 442a00f38d69
Create Date: 2026-10-05 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3e1a9d5b02'
down_revision: Union[str, None] = '442a00f38d69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply this migration."""
    op.create_table(
        'aircraft',
        sa.Column('registration', sa.String(length=20), nullable=False),
        sa.Column('type', sa.String(length=64), nullable=False),
        sa.Column('manufacturer', sa.String(length=64), nullable=False),
        sa.Column('operator', sa.String(length=128), nullable=True),
        sa.Column('flight_hours', sa.Float(), server_default='0', nullable=False),
        sa.Column('flight_cycles', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'current_technical_status',
            sa.String(length=32),
            server_default='SERVICEABLE',
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('registration', name='pk_aircraft'),
    )
    op.create_index(
        'ix_aircraft_registration', 'aircraft', ['registration'], unique=True
    )


def downgrade() -> None:
    """Revert this migration."""
    op.drop_index('ix_aircraft_registration', table_name='aircraft')
    op.drop_table('aircraft')
