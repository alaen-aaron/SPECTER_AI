"""M7.2: planned_actions objective/expected_value/risk_level/scan_id

Additive columns for AI-driven planning & controlled execution:
- objective        — the planner session's stated objective (audit trail)
- expected_value   — planner's rationale for information value
- risk_level       — deterministic risk classification of the proposal
- scan_id          — link to the Scan created when the action is executed

All nullable; existing rows are unaffected.

Revision ID: e5f6a7b8c9d0
Revises: d0e1f2a3b4c5
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "planned_actions",
        sa.Column("objective", sa.Text(), nullable=True),
    )
    op.add_column(
        "planned_actions",
        sa.Column("expected_value", sa.Text(), nullable=True),
    )
    op.add_column(
        "planned_actions",
        sa.Column("risk_level", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "planned_actions",
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("planned_actions", "scan_id")
    op.drop_column("planned_actions", "risk_level")
    op.drop_column("planned_actions", "expected_value")
    op.drop_column("planned_actions", "objective")
