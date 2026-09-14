"""M7.5 Phase 1: workflow & schedule safety hardening

Additive-only on the schedules table:
- schedules.expires_at: bounded lifetime for a repeating trigger. Once
  the clock passes this deadline the schedule is permanently disabled by
  the scheduler / ScheduleService and can never fire again.
- schedules.updated_at: last-touch timestamp (paused/resumed/advanced/
  disabled), matching the other mutable tables' convention.

Revision ID: c0d1e2f3a4b5
Revises: b5c6d7e8f9a0
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "schedules",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("schedules", "updated_at")
    op.drop_column("schedules", "expires_at")
