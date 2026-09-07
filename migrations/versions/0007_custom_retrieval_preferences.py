"""add custom retrieval history and preferences

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'custom_retrieval_events' not in tables:
        op.create_table(
            'custom_retrieval_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('exercise_key', sa.String(length=160), nullable=False),
            sa.Column('retrieved_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_custom_retrieval_events_user_id', 'custom_retrieval_events', ['user_id'])
        op.create_index('ix_custom_retrieval_events_exercise_key', 'custom_retrieval_events', ['exercise_key'])
        op.create_index('ix_custom_retrieval_events_retrieved_at', 'custom_retrieval_events', ['retrieved_at'])
        op.create_index('idx_custom_retrieval_event_user_date', 'custom_retrieval_events', ['user_id', 'retrieved_at'])
        op.create_index(
            'idx_custom_retrieval_event_user_exercise_date',
            'custom_retrieval_events',
            ['user_id', 'exercise_key', 'retrieved_at'],
        )

    if 'custom_retrieval_preferences' not in tables:
        op.create_table(
            'custom_retrieval_preferences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('sort_mode', sa.String(length=32), nullable=False, server_default='most_retrieved'),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id'),
        )
        op.create_index('ix_custom_retrieval_preferences_user_id', 'custom_retrieval_preferences', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_custom_retrieval_preferences_user_id', table_name='custom_retrieval_preferences')
    op.drop_table('custom_retrieval_preferences')
    op.drop_index('idx_custom_retrieval_event_user_exercise_date', table_name='custom_retrieval_events')
    op.drop_index('idx_custom_retrieval_event_user_date', table_name='custom_retrieval_events')
    op.drop_index('ix_custom_retrieval_events_retrieved_at', table_name='custom_retrieval_events')
    op.drop_index('ix_custom_retrieval_events_exercise_key', table_name='custom_retrieval_events')
    op.drop_index('ix_custom_retrieval_events_user_id', table_name='custom_retrieval_events')
    op.drop_table('custom_retrieval_events')
