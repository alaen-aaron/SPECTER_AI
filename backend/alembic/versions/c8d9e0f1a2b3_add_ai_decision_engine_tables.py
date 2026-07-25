"""add ai decision engine tables

Revision ID: c8d9e0f1a2b3
Revises: 4fb4b62191e5
Create Date: 2026-07-23 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = '4fb4b62191e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # planned_actions table
    op.create_table('planned_actions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('action_type', sa.String(length=50), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('justification', sa.Text(), nullable=False),
    sa.Column('plugin', sa.String(length=100), nullable=True),
    sa.Column('target_ids', postgresql.JSONB(), nullable=True),
    sa.Column('plugin_config', postgresql.JSONB(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_planned_actions_project_status', 'planned_actions', ['project_id', 'status'])

    # risk_scores table
    op.create_table('risk_scores',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('finding_id', sa.UUID(), nullable=False),
    sa.Column('base_score', sa.Numeric(4, 2), nullable=False),
    sa.Column('exposure_modifier', sa.Numeric(4, 2), nullable=False),
    sa.Column('ai_rationale', sa.Text(), nullable=True),
    sa.Column('review_status', sa.String(length=30), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_risk_scores_finding', 'risk_scores', ['finding_id'], unique=True)

    # prompt_templates table
    op.create_table('prompt_templates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('template_text', sa.Text(), nullable=False),
    sa.Column('required_variables', postgresql.JSONB(), nullable=True),
    sa.Column('expected_output_schema', postgresql.JSONB(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_prompt_templates_name_version', 'prompt_templates', ['name', 'version'], unique=True)

    # ai_context_memory table
    op.create_table('ai_context_memory',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('memory_type', sa.String(length=50), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('metadata', postgresql.JSONB(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ai_context_memory_project_type', 'ai_context_memory', ['project_id', 'memory_type'])


def downgrade() -> None:
    op.drop_index('idx_ai_context_memory_project_type', table_name='ai_context_memory')
    op.drop_table('ai_context_memory')
    op.drop_index('idx_prompt_templates_name_version', table_name='prompt_templates')
    op.drop_table('prompt_templates')
    op.drop_index('idx_risk_scores_finding', table_name='risk_scores')
    op.drop_table('risk_scores')
    op.drop_index('idx_planned_actions_project_status', table_name='planned_actions')
    op.drop_table('planned_actions')
