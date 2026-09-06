"""add bodyweight exercise preferences

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-06
"""

import sqlalchemy as sa
from alembic import op


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'workout_logs' in tables:
        log_columns = {col['name'] for col in inspector.get_columns('workout_logs')}
        if 'uses_bodyweight' not in log_columns:
            with op.batch_alter_table('workout_logs') as batch_op:
                batch_op.add_column(sa.Column('uses_bodyweight', sa.Boolean(), nullable=True))

    if 'bodyweight_exercise_preferences' not in tables:
        op.create_table(
            'bodyweight_exercise_preferences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('exercise_key', sa.String(length=160), nullable=False),
            sa.Column('exercise_name', sa.String(length=160), nullable=False),
            sa.Column('is_bodyweight', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('source', sa.String(length=32), nullable=False, server_default='manual'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )

    indexes = {idx['name'] for idx in inspector.get_indexes('bodyweight_exercise_preferences')}
    if 'ix_bodyweight_exercise_preferences_user_id' not in indexes:
        op.create_index('ix_bodyweight_exercise_preferences_user_id', 'bodyweight_exercise_preferences', ['user_id'])
    if 'ix_bodyweight_exercise_preferences_exercise_key' not in indexes:
        op.create_index('ix_bodyweight_exercise_preferences_exercise_key', 'bodyweight_exercise_preferences', ['exercise_key'])
    if 'idx_user_bodyweight_exercise_pref' not in indexes:
        op.create_index(
            'idx_user_bodyweight_exercise_pref',
            'bodyweight_exercise_preferences',
            ['user_id', 'exercise_key'],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index('idx_user_bodyweight_exercise_pref', table_name='bodyweight_exercise_preferences')
    op.drop_index('ix_bodyweight_exercise_preferences_exercise_key', table_name='bodyweight_exercise_preferences')
    op.drop_index('ix_bodyweight_exercise_preferences_user_id', table_name='bodyweight_exercise_preferences')
    op.drop_table('bodyweight_exercise_preferences')
    with op.batch_alter_table('workout_logs') as batch_op:
        batch_op.drop_column('uses_bodyweight')
