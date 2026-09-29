"""initial_schema

Revision ID: d5c3a4b4555c
Revises: 
Create Date: 2026-09-29 13:12:19.742991

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd5c3a4b4555c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Create background_jobs table if it doesn't exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'background_jobs' not in existing_tables:
        op.create_table(
            'background_jobs',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('job_type', sa.String(), nullable=False),
            sa.Column('status', sa.String(), server_default='pending', nullable=False),
            sa.Column('payload', sa.JSON(), nullable=True),
            sa.Column('result', sa.JSON(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('attempts', sa.Integer(), server_default='0', nullable=True),
            sa.Column('max_attempts', sa.Integer(), server_default='3', nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('background_jobs', schema=None) as batch_op:
            batch_op.create_index('ix_background_jobs_created_at', ['created_at'], unique=False)
            batch_op.create_index('ix_background_jobs_id', ['id'], unique=False)
            batch_op.create_index('ix_background_jobs_job_type', ['job_type'], unique=False)
            batch_op.create_index('ix_background_jobs_status', ['status'], unique=False)

    # 2. Create idempotency_records table if it doesn't exist
    if 'idempotency_records' not in existing_tables:
        op.create_table(
            'idempotency_records',
            sa.Column('idempotency_key', sa.String(), nullable=False),
            sa.Column('request_path', sa.String(), nullable=False),
            sa.Column('request_hash', sa.String(), nullable=False),
            sa.Column('status_code', sa.Integer(), nullable=False),
            sa.Column('response_headers', sa.JSON(), nullable=True),
            sa.Column('response_body', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('idempotency_key')
        )
        with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
            batch_op.create_index('ix_idempotency_records_expires_at', ['expires_at'], unique=False)
            batch_op.create_index('ix_idempotency_records_idempotency_key', ['idempotency_key'], unique=False)

    # 3. Upgrade agents table
    with op.batch_alter_table('agents', schema=None) as batch_op:
        cols = [c['name'] for c in inspector.get_columns('agents')]
        if 'is_deleted' not in cols:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        if 'deleted_at' not in cols:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in cols:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch_op.create_index('ix_agents_is_deleted', ['is_deleted'], unique=False)
        batch_op.create_index('ix_agents_name', ['name'], unique=False)

    # 4. Upgrade corrections table
    with op.batch_alter_table('corrections', schema=None) as batch_op:
        cols = [c['name'] for c in inspector.get_columns('corrections')]
        if 'is_deleted' not in cols:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        if 'deleted_at' not in cols:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in cols:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch_op.create_index('ix_corrections_interaction_id', ['interaction_id'], unique=False)

    # 5. Upgrade interactions table
    with op.batch_alter_table('interactions', schema=None) as batch_op:
        cols = [c['name'] for c in inspector.get_columns('interactions')]
        if 'is_deleted' not in cols:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        if 'deleted_at' not in cols:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in cols:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch_op.create_index('ix_interactions_agent_created', ['agent_id', 'created_at'], unique=False)
        batch_op.create_index('ix_interactions_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_interactions_customer_tier', ['customer_tier'], unique=False)
        batch_op.create_index('ix_interactions_is_deleted', ['is_deleted'], unique=False)
        batch_op.create_index('ix_interactions_task_tier', ['task_type', 'customer_tier'], unique=False)

    # 6. Upgrade lesson_candidates table
    with op.batch_alter_table('lesson_candidates', schema=None) as batch_op:
        cols = [c['name'] for c in inspector.get_columns('lesson_candidates')]
        if 'is_deleted' not in cols:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        if 'deleted_at' not in cols:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in cols:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch_op.create_index('ix_candidates_validated_promoted', ['is_validated', 'is_promoted'], unique=False)
        batch_op.create_index('ix_lesson_candidates_source_agent', ['source_agent'], unique=False)
        batch_op.create_index('ix_lesson_candidates_task_type', ['task_type'], unique=False)

    # 7. Upgrade memory_references table
    with op.batch_alter_table('memory_references', schema=None) as batch_op:
        cols = [c['name'] for c in inspector.get_columns('memory_references')]
        if 'is_deleted' not in cols:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        if 'deleted_at' not in cols:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in cols:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch_op.create_index('ix_memory_references_confidence_level', ['confidence_level'], unique=False)
        batch_op.create_index('ix_memory_references_confidence_score', ['confidence_score'], unique=False)
        batch_op.create_index('ix_memory_references_is_deleted', ['is_deleted'], unique=False)
        batch_op.create_index('ix_memory_references_memory_type', ['memory_type'], unique=False)
        batch_op.create_index('ix_memory_references_source_agent', ['source_agent'], unique=False)
        batch_op.create_index('ix_memory_references_task_type', ['task_type'], unique=False)
        batch_op.create_index('ix_memory_task_confidence', ['task_type', 'confidence_score'], unique=False)

    # 8. Upgrade outcomes table
    with op.batch_alter_table('outcomes', schema=None) as batch_op:
        cols = [c['name'] for c in inspector.get_columns('outcomes')]
        if 'is_deleted' not in cols:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        if 'deleted_at' not in cols:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in cols:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch_op.create_index('ix_outcomes_memory_id', ['memory_id'], unique=False)
        batch_op.create_index('ix_outcomes_outcome_type', ['outcome_type'], unique=False)

    # 9. Upgrade system_events table
    with op.batch_alter_table('system_events', schema=None) as batch_op:
        batch_op.create_index('ix_events_type_created', ['event_type', 'created_at'], unique=False)

def downgrade() -> None:
    op.drop_table('idempotency_records')
    op.drop_table('background_jobs')
