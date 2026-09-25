"""add stats page preferences (sort mode, time range) and exercise view counts

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'stats_preferences' not in tables:
        op.create_table(
            'stats_preferences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('sort_mode', sa.String(length=32), nullable=False, server_default='most_viewed'),
            sa.Column('time_range', sa.String(length=8), nullable=False, server_default='all'),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id'),
        )
        op.create_index('ix_stats_preferences_user_id', 'stats_preferences', ['user_id'])
    elif 'time_range' not in {col['name'] for col in inspector.get_columns('stats_preferences')}:
        # Table may already exist if the app's startup schema check created it first.
        op.add_column(
            'stats_preferences',
            sa.Column('time_range', sa.String(length=8), nullable=False, server_default='all'),
        )

    if 'stats_exercise_views' not in tables:
        op.create_table(
            'stats_exercise_views',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('exercise_key', sa.String(length=160), nullable=False),
            sa.Column('view_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_viewed_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_stats_exercise_views_user_id', 'stats_exercise_views', ['user_id'])
        op.create_index(
            'idx_stats_exercise_view_user_exercise',
            'stats_exercise_views',
            ['user_id', 'exercise_key'],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index('idx_stats_exercise_view_user_exercise', table_name='stats_exercise_views')
    op.drop_index('ix_stats_exercise_views_user_id', table_name='stats_exercise_views')
    op.drop_table('stats_exercise_views')
    op.drop_index('ix_stats_preferences_user_id', table_name='stats_preferences')
    op.drop_table('stats_preferences')
