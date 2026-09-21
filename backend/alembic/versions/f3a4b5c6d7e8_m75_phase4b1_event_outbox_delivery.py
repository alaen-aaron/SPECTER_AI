"""M7.5 Phase 4-B1: outbox claim/lease infrastructure for event delivery

Extends the Phase 4-A `event_outbox` table with the delivery/claim columns
needed to run an at-most-once consumer later (Phase 4-B2). This revision only
adds the durable machinery: claim/lease state, attempt accounting, and backoff
watermarks. No consumer, dispatcher, HTTP delivery, or notifications exist
yet — nothing in the runtime writes delivery transitions in this phase, so all
new columns are server-defaulted to keep the observation guarantee of Phase
4-A intact (a committed row stays proof its transition committed and is
immediately claimable).

Key choices:
- every new column is additive and server-defaulted: `available_after` =
  `now()`, `status = 'pending'`, `attempts = 0`, `max_attempts = 10`,
  `specversion = '1.0'`. Rows written by Phase 4-A producers are claimable
  with no producer changes.
- `next_retry_at` doubles as the lease watermark: the claimer sets it to
  `now() + lease`; `requeue_expired` revives `delivering` rows whose watermark
  passed, `pending` rows treat it as a retry backoff gate, and
  `delivered/dead_letter` rows keep it `NULL`.
- the partial index `idx_event_outbox_delivery (status, available_after,
  next_retry_at) WHERE status IN ('pending','delivering')` keeps the claim
  query covering the rows that can ever be claimed without bloating the table
  with delivered/dead-letter history.
- `scan_id` mirrors the optional entity-reference style of 4-A (plain indexed
  UUID, no FK): an append-only observation trail must never be blocked by the
  lifecycle of its subjects.
- `delivered_at` is the completion time, `NULL` while the event is
  pending/delivering/dead-lettered (a dead letter has no delivery).

No destructive changes: existing columns, indexes, keys, and the public API
are untouched. Downgrade drops only what this revision added.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event_outbox",
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "event_outbox",
        sa.Column(
            "specversion",
            sa.String(length=5),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "event_outbox",
        sa.Column(
            "available_after",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "event_outbox",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "event_outbox",
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "event_outbox",
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )
    op.add_column(
        "event_outbox",
        sa.Column("last_error", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "event_outbox",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "event_outbox",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_event_outbox_delivery",
        "event_outbox",
        ["status", "available_after", "next_retry_at"],
        postgresql_where=sa.text("status IN ('pending', 'delivering')"),
    )


def downgrade() -> None:
    op.drop_index("idx_event_outbox_delivery", table_name="event_outbox")
    op.drop_column("event_outbox", "delivered_at")
    op.drop_column("event_outbox", "next_retry_at")
    op.drop_column("event_outbox", "last_error")
    op.drop_column("event_outbox", "max_attempts")
    op.drop_column("event_outbox", "attempts")
    op.drop_column("event_outbox", "status")
    op.drop_column("event_outbox", "available_after")
    op.drop_column("event_outbox", "specversion")
    op.drop_column("event_outbox", "scan_id")