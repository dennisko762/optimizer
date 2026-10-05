"""create aircraft table

Revision ID: 442a00f38d69
Revises:
Create Date: 2026-10-05 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "442a00f38d69"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aircraft",
        sa.Column("registration", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("manufacturer", sa.String(), nullable=True),
        sa.Column("operator", sa.String(), nullable=True),
        sa.Column("flight_hours", sa.Float(), nullable=True),
        sa.Column("flight_cycles", sa.Integer(), nullable=True),
        sa.Column(
            "current_technical_status",
            sa.String(),
            server_default="SERVICEABLE",
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("registration"),
    )


def downgrade() -> None:
    op.drop_table("aircraft")
