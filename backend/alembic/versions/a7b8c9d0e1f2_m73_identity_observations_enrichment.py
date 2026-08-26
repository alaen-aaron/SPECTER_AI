"""M7.3 Phase 2: asset identity, observations, finding enrichment

Additive-only:
- assets.identity_key (nullable) + partial unique index on
  (project_id, asset_type, identity_key) WHERE identity_key IS NOT NULL,
  backfilled in Python using EXACTLY the same rules as
  `app.domain.asset_identity` so application and DB agree.
- findings.enrichment JSONB (nullable) — Phase 3 foundation.
- asset_observations table: append-only (ToolResult, Asset) provenance
  with uq(tool_result_id, asset_id) idempotency and lookup indexes.

Revision ID: a7b8c9d0e1f2
Revises: e5f6a7b8c9d0
Create Date: 2026-08-25
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from urllib.parse import urlsplit

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_SERVICE_RE = re.compile(
    r"^(?P<service>[^:/]+)://(?P<host>[^:/]+):(?P<port>\d+)/(?P<proto>[a-z0-9]+)$",
    re.IGNORECASE,
)


def _slug(value: str) -> str:
    out: list[str] = []
    prev_dash = False
    for ch in value.strip().lower():
        if ch.isalnum() or ch in "._":
            out.append(ch)
            prev_dash = False
        elif not prev_dash and out:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _identity_for(asset_type: str, value: str) -> str | None:
    """Mirror of app.domain.asset_identity.identity_for_asset."""
    text = (value or "").strip()
    if not text:
        return None
    if asset_type in ("host", "subdomain"):
        try:
            import ipaddress

            return str(ipaddress.ip_address(text))
        except ValueError:
            if "://" in text:
                parts = urlsplit(text)
                text = parts.hostname or text
            return text.strip().strip(".").lower()
    if asset_type == "service":
        legacy = _LEGACY_SERVICE_RE.match(text)
        if legacy:
            return (
                f"{legacy.group('proto').lower()}/"
                f"{legacy.group('host').lower()}:{legacy.group('port')}"
            )
        if "://" in text:
            parts = urlsplit(text)
            host = parts.hostname
            port = parts.port or (
                443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None
            )
            if host and port:
                return f"tcp/{host.lower()}:{port}/{parts.scheme.lower()}"
        return _slug(text)
    return _slug(text)


def _backfill_identity() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, asset_type, value FROM assets WHERE identity_key IS NULL"
        )
    ).fetchall()
    updates: list[dict[str, object]] = []
    seen: set[tuple[object, object, object]] = set()
    for row_id, asset_type, value in rows:
        key = _identity_for(asset_type, value)
        if key is None:
            continue
        marker = (row_id, asset_type, key)
        if marker in seen:
            continue
        seen.add(marker)
        updates.append({"id": row_id, "key": key})
    if updates:
        bind.execute(
            sa.text("UPDATE assets SET identity_key = :key WHERE id = :id"),
            updates,
        )


def upgrade() -> None:
    # --- assets.identity_key -------------------------------------------------
    op.add_column(
        "assets",
        sa.Column("identity_key", sa.String(length=500), nullable=True),
    )
    _backfill_identity()
    op.create_index(
        "uq_asset_identity",
        "assets",
        ["project_id", "asset_type", "identity_key"],
        unique=True,
        postgresql_where=sa.text("identity_key IS NOT NULL"),
    )

    # --- findings.enrichment -------------------------------------------------
    op.add_column(
        "findings",
        sa.Column("enrichment", postgresql.JSONB(), nullable=True),
    )

    # --- asset_observations --------------------------------------------------
    op.create_table(
        "asset_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plugin", sa.String(length=100), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "details", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.UniqueConstraint(
            "tool_result_id",
            "asset_id",
            name="uq_asset_observation_tool_result_asset",
        ),
    )
    op.create_index(
        "idx_asset_observations_asset_time",
        "asset_observations",
        ["asset_id", "observed_at"],
    )
    op.create_index(
        "idx_asset_observations_tool_result",
        "asset_observations",
        ["tool_result_id"],
    )
    op.create_index(
        "idx_asset_observations_project",
        "asset_observations",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_asset_observations_project", table_name="asset_observations")
    op.drop_index("idx_asset_observations_tool_result", table_name="asset_observations")
    op.drop_index("idx_asset_observations_asset_time", table_name="asset_observations")
    op.drop_table("asset_observations")
    op.drop_column("findings", "enrichment")
    op.drop_index("uq_asset_identity", table_name="assets")
    op.drop_column("assets", "identity_key")
