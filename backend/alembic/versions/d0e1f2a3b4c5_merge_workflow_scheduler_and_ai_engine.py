"""merge workflow scheduler and ai engine

Revision ID: d0e1f2a3b4c5
Revises: a1b2c3d4e5f6, c8d9e0f1a2b3
Create Date: 2026-07-24 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', 'c8d9e0f1a2b3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
