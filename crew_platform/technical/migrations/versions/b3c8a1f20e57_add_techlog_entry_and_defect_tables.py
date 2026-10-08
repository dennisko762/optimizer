"""add techlog_entry and defect tables

Revision ID: b3c8a1f20e57
Revises: 442a00f38d69
Create Date: 2026-10-05 13:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3c8a1f20e57"
down_revision: Union[str, None] = "442a00f38d69"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "techlog_entry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("aircraft_registration", sa.String(), nullable=False),
        sa.Column("airline_style_log_line_id", sa.String(), nullable=True),
        sa.Column("phase", sa.String(), nullable=True),
        sa.Column(
            "status", sa.String(), server_default="OPEN", nullable=False
        ),
        sa.Column("pilot_report", sa.Text(), nullable=True),
        sa.Column("chapter", sa.String(), nullable=True),
        sa.Column("system_component", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("closure_timestamp", sa.DateTime(), nullable=True),
        sa.Column("mel_reference", sa.String(), nullable=True),
        sa.Column("maintenance_action", sa.Text(), nullable=True),
        sa.Column("free_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["aircraft_registration"], ["aircraft.registration"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_techlog_entry_aircraft_registration",
        "techlog_entry",
        ["aircraft_registration"],
    )

    op.create_table(
        "defect",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("techlog_entry_id", sa.Integer(), nullable=False),
        sa.Column("aircraft_registration", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(), server_default="OPEN", nullable=False
        ),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("chapter", sa.String(), nullable=True),
        sa.Column("system_component", sa.String(), nullable=True),
        sa.Column("pilot_report", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("mel_reference", sa.String(), nullable=True),
        sa.Column("maintenance_action", sa.Text(), nullable=True),
        sa.Column("closed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("closure_timestamp", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["techlog_entry_id"], ["techlog_entry.id"]
        ),
        sa.ForeignKeyConstraint(
            ["aircraft_registration"], ["aircraft.registration"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_defect_techlog_entry_id", "defect", ["techlog_entry_id"]
    )
    op.create_index(
        "ix_defect_aircraft_registration", "defect", ["aircraft_registration"]
    )


def downgrade() -> None:
    op.drop_index("ix_defect_aircraft_registration", table_name="defect")
    op.drop_index("ix_defect_techlog_entry_id", table_name="defect")
    op.drop_table("defect")
    op.drop_index(
        "ix_techlog_entry_aircraft_registration", table_name="techlog_entry"
    )
    op.drop_table("techlog_entry")
