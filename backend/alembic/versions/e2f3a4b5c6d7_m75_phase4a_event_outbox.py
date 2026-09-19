"""M7.5 Phase 4-A: durable transactional event outbox

Additive-only: one new table, `event_outbox`, the observation substrate
for campaign lifecycle events. Every row is written in the same
transaction as the domain state change it describes, so a committed
event is proof the transition committed. Phase 4-A stores events only —
no delivery, no status columns, no consumers.

Key choices:
- entity references (organizations/projects/schedules/autonomous_runs)
  are plain indexed UUID columns, NOT foreign keys: an append-only
  observation trail must never block or be blocked by the lifecycle of
  its subjects (a project/run can be deleted independently and the
  event history survives untouched). Reference integrity is guaranteed
  by the writer, which always derives `organization_id` from a live
  project row.
- `occurred_at` is the business event time (the moment the transition
  happened); `created_at` is the storage time (server default).
- payload is a plain JSONB document produced by whitelisted builders.

No destructive changes: existing tables, indexes, keys, and the public
API are all untouched.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("autonomous_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_event_outbox_org_time", "event_outbox", ["organization_id", "created_at"]
    )
    op.create_index("idx_event_outbox_project", "event_outbox", ["project_id"])
    op.create_index("idx_event_outbox_run", "event_outbox", ["autonomous_run_id"])
    op.create_index("idx_event_outbox_type", "event_outbox", ["event_type"])


def downgrade() -> None:
    op.drop_index("idx_event_outbox_type", table_name="event_outbox")
    op.drop_index("idx_event_outbox_run", table_name="event_outbox")
    op.drop_index("idx_event_outbox_project", table_name="event_outbox")
    op.drop_index("idx_event_outbox_org_time", table_name="event_outbox")
    op.drop_table("event_outbox")