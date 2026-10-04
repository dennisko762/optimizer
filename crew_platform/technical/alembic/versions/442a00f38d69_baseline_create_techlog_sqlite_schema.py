"""baseline: create TechLog SQLite schema

Baseline marker migration for the crew_platform TechLog domain.

No domain tables exist yet — the TechLog/Aircraft ORM models are delivered in
a later ticket, so this migration intentionally creates no tables. Running
``alembic upgrade head`` here still:
  1. creates the SQLite database file at the env-configured path, and
  2. creates the ``alembic_version`` bookkeeping table so the schema is
     tracked from a known baseline.

When the domain models land (crew_platform/technical/models.py), the next
migration generated from them will create the real tables on top of this
baseline.

Revision ID: 442a00f38d69
Revises:
Create Date: 2026-10-04 23:26:04.797623

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '442a00f38d69'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply this migration."""
    pass


def downgrade() -> None:
    """Revert this migration."""
    pass
