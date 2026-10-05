"""add maintenance_action table

Revision ID: 7d2e4c91b3a6
Revises: b3c8a1f20e57
Create Date: 2026-10-05 16:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7d2e4c91b3a6"
down_revision: Union[str, None] = "b3c8a1f20e57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maintenance_action",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("aircraft_registration", sa.String(), nullable=False),
        sa.Column("defect_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("performed_by", sa.String(), nullable=False),
        sa.Column(
            "performed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["aircraft_registration"], ["aircraft.registration"]
        ),
        sa.ForeignKeyConstraint(["defect_id"], ["defect.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_action_aircraft_registration",
        "maintenance_action",
        ["aircraft_registration"],
    )
    op.create_index(
        "ix_maintenance_action_defect_id",
        "maintenance_action",
        ["defect_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_maintenance_action_defect_id", table_name="maintenance_action"
    )
    op.drop_index(
        "ix_maintenance_action_aircraft_registration",
        table_name="maintenance_action",
    )
    op.drop_table("maintenance_action")
