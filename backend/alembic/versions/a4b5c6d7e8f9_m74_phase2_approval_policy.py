"""M7.4 Phase 2: autonomous approval-policy metadata

Additive-only on `autonomous_run_actions`:
- approval_mode: how an action was approved (manual | auto_policy) —
  the autonomous audit trail must never fabricate human approval.
- planned_action_id: durable linkage to the M7.2 PlannedAction that
  was routed through execute_approved().

Revision ID: a4b5c6d7e8f9
Revises: f1a2b3c4d5e6
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autonomous_run_actions",
        sa.Column("approval_mode", sa.String(20), nullable=True),
    )
    op.add_column(
        "autonomous_run_actions",
        sa.Column(
            "planned_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("planned_actions.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_autonomous_actions_approval_mode",
        "autonomous_run_actions",
        ["approval_mode"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_autonomous_actions_approval_mode",
        table_name="autonomous_run_actions",
    )
    op.drop_column("autonomous_run_actions", "planned_action_id")
    op.drop_column("autonomous_run_actions", "approval_mode")