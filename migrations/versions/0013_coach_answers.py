"""coach_answers: each admin AI coach answer, with an optional rating, for reviewing quality

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy_utils import JSONType


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # The app's startup schema check may have created it already.
    if 'coach_answers' not in set(inspector.get_table_names()):
        op.create_table(
            'coach_answers',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('question', sa.Text(), nullable=False),
            sa.Column('answer', sa.Text(), nullable=False),
            sa.Column('source', sa.String(length=8), nullable=False, server_default='ai'),
            sa.Column('model', sa.String(length=80), nullable=True),
            sa.Column('steps', JSONType(), nullable=True),
            sa.Column('calls', sa.Integer(), nullable=True),
            sa.Column('tokens', sa.Integer(), nullable=True),
            sa.Column('ms', sa.Integer(), nullable=True),
            sa.Column('failed', sa.String(length=300), nullable=True),
            sa.Column('rating', sa.Integer(), nullable=True),
            sa.Column('comment', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_coach_answers_user_id', 'coach_answers', ['user_id'])
        op.create_index('ix_coach_answers_rating', 'coach_answers', ['rating'])
        op.create_index('ix_coach_answers_created_at', 'coach_answers', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_coach_answers_created_at', table_name='coach_answers')
    op.drop_index('ix_coach_answers_rating', table_name='coach_answers')
    op.drop_index('ix_coach_answers_user_id', table_name='coach_answers')
    op.drop_table('coach_answers')
