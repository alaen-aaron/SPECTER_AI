"""add workflow scheduler correlation tables

Revision ID: a1b2c3d4e5f6
Revises: 4fb4b62191e5
Create Date: 2026-07-23 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '4fb4b62191e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- workflows ---
    op.create_table('workflows',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_workflows_project', 'workflows', ['project_id'], unique=False)
    op.create_index('idx_workflows_status', 'workflows', ['status'], unique=False)

    # --- workflow_steps ---
    op.create_table('workflow_steps',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workflow_id', sa.UUID(), nullable=False),
    sa.Column('step_type', sa.String(length=20), nullable=False),
    sa.Column('plugin', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('plugin_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('depends_on', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_workflow_steps_workflow', 'workflow_steps', ['workflow_id'], unique=False)

    # --- workflow_executions ---
    op.create_table('workflow_executions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workflow_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('initiated_by', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('step_results', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['initiated_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_workflow_executions_workflow', 'workflow_executions', ['workflow_id'], unique=False)
    op.create_index('idx_workflow_executions_project', 'workflow_executions', ['project_id'], unique=False)
    op.create_index('idx_workflow_executions_status', 'workflow_executions', ['status'], unique=False)

    # --- schedules ---
    op.create_table('schedules',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workflow_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('frequency', sa.String(length=20), nullable=False),
    sa.Column('cron_expression', sa.String(length=100), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_schedules_workflow', 'schedules', ['workflow_id'], unique=False)
    op.create_index('idx_schedules_project', 'schedules', ['project_id'], unique=False)
    op.create_index('idx_schedules_active_next', 'schedules', ['is_active', 'next_run_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_schedules_active_next', table_name='schedules')
    op.drop_index('idx_schedules_project', table_name='schedules')
    op.drop_index('idx_schedules_workflow', table_name='schedules')
    op.drop_table('schedules')
    op.drop_index('idx_workflow_executions_status', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_project', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_workflow', table_name='workflow_executions')
    op.drop_table('workflow_executions')
    op.drop_index('idx_workflow_steps_workflow', table_name='workflow_steps')
    op.drop_table('workflow_steps')
    op.drop_index('idx_workflows_status', table_name='workflows')
    op.drop_index('idx_workflows_project', table_name='workflows')
    op.drop_table('workflows')
