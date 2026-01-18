"""baseline

Revision ID: 0001
Revises: 
Create Date: 2026-01-18
"""


from alembic import op
import sqlalchemy as sa
from sqlalchemy_utils import JSONType


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('password_hash', sa.String(length=255), nullable=True),
        sa.Column(
            'role',
            sa.Enum('user', 'admin', name='userrole', native_enum=False),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('verification_token', sa.String(length=255), nullable=True),
        sa.Column('verification_token_expires', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('username', name='uq_users_username'),
        sa.UniqueConstraint('email', name='uq_users_email'),
    )

    op.create_index('ix_users_username', 'users', ['username'])
    op.create_index('ix_users_email', 'users', ['email'])

    op.create_table(
        'lifts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('exercise', sa.String(length=100), nullable=False),
        sa.Column('best_string', sa.Text(), nullable=True),
        sa.Column('sets_json', JSONType(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    op.create_index('ix_lifts_exercise', 'lifts', ['exercise'])
    op.create_index('ix_lifts_updated_at', 'lifts', ['updated_at'])
    op.create_index('idx_user_exercise', 'lifts', ['user_id', 'exercise'])

    op.create_table(
        'plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('text_content', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('user_id', name='uq_plans_user_id'),
    )

    op.create_index('ix_plans_user_id', 'plans', ['user_id'])

    op.create_table(
        'rep_ranges',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('text_content', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('user_id', name='uq_rep_ranges_user_id'),
    )

    op.create_index('ix_rep_ranges_user_id', 'rep_ranges', ['user_id'])

    op.create_table(
        'workout_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('exercise', sa.String(length=100), nullable=False),
        sa.Column('top_weight', sa.Float(), nullable=True),
        sa.Column('top_reps', sa.Integer(), nullable=True),
        sa.Column('estimated_1rm', sa.Float(), nullable=True),
    )

    op.create_index('ix_workout_logs_user_id', 'workout_logs', ['user_id'])
    op.create_index('ix_workout_logs_date', 'workout_logs', ['date'])
    op.create_index('ix_workout_logs_exercise', 'workout_logs', ['exercise'])
    op.create_index('ix_workout_logs_estimated_1rm', 'workout_logs', ['estimated_1rm'])
    op.create_index('idx_user_date', 'workout_logs', ['user_id', 'date'])
    op.create_index('idx_user_exercise_date', 'workout_logs', ['user_id', 'exercise', 'date'])


def downgrade() -> None:
    op.drop_index('idx_user_exercise_date', table_name='workout_logs')
    op.drop_index('idx_user_date', table_name='workout_logs')
    op.drop_index('ix_workout_logs_estimated_1rm', table_name='workout_logs')
    op.drop_index('ix_workout_logs_exercise', table_name='workout_logs')
    op.drop_index('ix_workout_logs_date', table_name='workout_logs')
    op.drop_index('ix_workout_logs_user_id', table_name='workout_logs')
    op.drop_table('workout_logs')

    op.drop_index('ix_rep_ranges_user_id', table_name='rep_ranges')
    op.drop_table('rep_ranges')

    op.drop_index('ix_plans_user_id', table_name='plans')
    op.drop_table('plans')

    op.drop_index('idx_user_exercise', table_name='lifts')
    op.drop_index('ix_lifts_updated_at', table_name='lifts')
    op.drop_index('ix_lifts_exercise', table_name='lifts')
    op.drop_table('lifts')

    op.drop_index('ix_users_email', table_name='users')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
