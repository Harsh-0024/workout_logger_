"""add workout_name to workout logs

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-20
"""

from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('workout_logs', sa.Column('workout_name', sa.String(length=100), nullable=True))
    op.create_index('ix_workout_logs_workout_name', 'workout_logs', ['workout_name'])


def downgrade() -> None:
    op.drop_index('ix_workout_logs_workout_name', table_name='workout_logs')
    op.drop_column('workout_logs', 'workout_name')
