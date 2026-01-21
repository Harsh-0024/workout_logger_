"""add exercise_string and sets_json to workout logs

Revision ID: 0003
Revises: 0002
Create Date: 2026-01-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy_utils import JSONType

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('workout_logs', sa.Column('exercise_string', sa.Text(), nullable=True))
    op.add_column('workout_logs', sa.Column('sets_json', JSONType(), nullable=True))


def downgrade() -> None:
    op.drop_column('workout_logs', 'sets_json')
    op.drop_column('workout_logs', 'exercise_string')
