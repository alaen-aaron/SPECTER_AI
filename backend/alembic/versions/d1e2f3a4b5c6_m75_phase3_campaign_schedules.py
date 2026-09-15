"""M7.5 Phase 3: scheduled autonomous campaigns

Additive-only changes to the schedules table that let a schedule create
an autonomous run (a "campaign") instead of a workflow execution:

- schedules.kind: 'workflow' | 'campaign'. Existing rows default to
  'workflow', preserving current semantics exactly.
- schedules.workflow_id: becomes NULLABLE — a CAMPAIGN schedule has no
  workflow; its trigger payload comes from `campaign_config` instead.
- schedules.campaign_config: JSONB holding the campaign's objective and
  budgets (max_actions, max_runtime_seconds), mirroring the interactive
  CreateAutonomousRunRequest bounds. NULL for workflow kind.

No destructive changes: the workflow-kind behaviour, indexes, and the
public API for workflow schedules are all untouched.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="workflow",
        ),
    )
    op.add_column(
        "schedules",
        sa.Column("campaign_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.alter_column("schedules", "workflow_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    # Campaign rows forbid NULL in workflow_id, so this only succeeds when
    # the operator has removed/backfilled the campaign schedules first.
    op.alter_column("schedules", "workflow_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("schedules", "campaign_config")
    op.drop_column("schedules", "kind")