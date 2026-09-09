"""M7.4 Phase 4: failure recovery, retry, and concurrency controls

Additive-only on existing autonomous/scan tables:
- scans.failure_kind: execution-time failure classification
  (transport | tool | domain) set by the ExecutionEngine. TRANSPORT is
  the only retryable kind — the plugin never ran.
- autonomous_run_actions.retry_count: capped (default 1) transport-retry
  counter per autonomous action.
- uq_autonomous_runs_active_project: partial unique index enforcing at
  most one non-terminal run per project at the DATABASE layer (the
  app-level check was a check-then-insert a concurrent pair could race).
  Terminal rows are excluded, so completing/cancelling/failing a run
  frees the slot automatically — no trigger logic needed.
- uq_autonomous_actions_planned_action: partial unique index ensuring
  one autonomous action per M7.2 PlannedAction (idempotency safety net).

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _ACTIVE_RUN_WHERE = "status NOT IN ('completed', 'cancelled', 'failed')"

    op.add_column(
        "scans",
        sa.Column("failure_kind", sa.String(30), nullable=True),
    )
    op.add_column(
        "autonomous_run_actions",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.create_index(
        "uq_autonomous_runs_active_project",
        "autonomous_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_RUN_WHERE),
    )
    op.create_index(
        "uq_autonomous_actions_planned_action",
        "autonomous_run_actions",
        ["planned_action_id"],
        unique=True,
        postgresql_where=sa.text("planned_action_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_autonomous_actions_planned_action",
        table_name="autonomous_run_actions",
    )
    op.drop_index(
        "uq_autonomous_runs_active_project",
        table_name="autonomous_runs",
    )
    op.drop_column("autonomous_run_actions", "retry_count")
    op.drop_column("scans", "failure_kind")