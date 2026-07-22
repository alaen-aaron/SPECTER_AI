"""evidence table (milestone 5)

Revision ID: d5e6f7a8b9c0
Revises: b7c3d4e5f6a1
Create Date: 2026-07-22 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "b7c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.UUID(), nullable=False),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("storage_pointer", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("collected_by", sa.UUID(), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["collected_by"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_evidence_finding", "evidence", ["finding_id"], unique=False)
    op.create_index("idx_evidence_hash", "evidence", ["content_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_evidence_hash", table_name="evidence")
    op.drop_index("idx_evidence_finding", table_name="evidence")
    op.drop_table("evidence")
