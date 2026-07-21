"""tool results, assets, findings (milestone 4)

Revision ID: b7c3d4e5f6a1
Revises: 5a5919956ad5
Create Date: 2026-07-21 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7c3d4e5f6a1"
down_revision: Union[str, None] = "5a5919956ad5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- tool_results ---
    op.create_table(
        "tool_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("plugin", sa.String(length=100), nullable=False),
        sa.Column("target", sa.String(length=500), nullable=False),
        sa.Column("normalized_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_output_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tool_results_scan", "tool_results", ["scan_id"], unique=False)
    op.create_index("idx_tool_results_plugin", "tool_results", ["plugin"], unique=False)

    # --- assets ---
    op.create_table(
        "assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("asset_type", sa.String(length=30), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("in_scope", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source_scan_id", sa.UUID(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_scan_id"], ["scans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "asset_type", "value", name="uq_asset_dedup"),
    )
    op.create_index("idx_assets_project_type", "assets", ["project_id", "asset_type"], unique=False)

    # --- findings ---
    op.create_table(
        "findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'open'")),
        sa.Column("cvss_score", sa.Numeric(3, 1), nullable=True),
        sa.Column("dedup_key", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_findings_project_severity", "findings", ["project_id", "severity"], unique=False)
    op.create_index("idx_findings_dedup", "findings", ["project_id", "dedup_key"], unique=True)

    # --- finding_tool_results (association table) ---
    op.create_table(
        "finding_tool_results",
        sa.Column("finding_id", sa.UUID(), nullable=False),
        sa.Column("tool_result_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_result_id"], ["tool_results.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("finding_id", "tool_result_id"),
    )


def downgrade() -> None:
    op.drop_table("finding_tool_results")
    op.drop_index("idx_findings_dedup", table_name="findings")
    op.drop_index("idx_findings_project_severity", table_name="findings")
    op.drop_table("findings")
    op.drop_index("idx_assets_project_type", table_name="assets")
    op.drop_table("assets")
    op.drop_index("idx_tool_results_plugin", table_name="tool_results")
    op.drop_index("idx_tool_results_scan", table_name="tool_results")
    op.drop_table("tool_results")
