"""M7.4 Phase 1: autonomous orchestration tables

Additive-only:
- autonomous_runs: durable state for AI-driven scan sessions
- autonomous_run_actions: per-action lifecycle within a run

Revision ID: f1a2b3c4d5e6
Revises: a7b8c9d0e1f2
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autonomous_runs
    op.create_table(
        "autonomous_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "initiated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="created"),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("max_actions", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("current_cycle", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approval_policy", sa.String(50), nullable=False, server_default="policy_based"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_autonomous_runs_project", "autonomous_runs", ["project_id"])
    op.create_index("idx_autonomous_runs_status", "autonomous_runs", ["status"])
    op.create_index(
        "idx_autonomous_runs_active_project",
        "autonomous_runs",
        ["project_id", "status"],
    )

    # autonomous_run_actions
    op.create_table(
        "autonomous_run_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomous_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cycle", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("plugin", sa.String(100), nullable=True),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("justification", sa.Text(), nullable=False, server_default=""),
        sa.Column("plugin_config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("target_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("category", sa.String(20), nullable=False, server_default="category_0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="proposed"),
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id"),
            nullable=True,
        ),
        sa.Column("result_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_autonomous_actions_run", "autonomous_run_actions", ["run_id"])
    op.create_index("idx_autonomous_actions_project", "autonomous_run_actions", ["project_id"])
    op.create_index("idx_autonomous_actions_status", "autonomous_run_actions", ["status"])


def downgrade() -> None:
    op.drop_table("autonomous_run_actions")
    op.drop_table("autonomous_runs")
