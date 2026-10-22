"""feedback: what users like, dislike, would improve or add, for the admin to read

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # The app's startup schema check may have created it already.
    if 'feedback' not in set(inspector.get_table_names()):
        op.create_table(
            'feedback',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(length=16), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('page', sa.String(length=200), nullable=True),
            sa.Column('status', sa.String(length=8), nullable=False, server_default='new'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_feedback_user_id', 'feedback', ['user_id'])
        op.create_index('ix_feedback_status', 'feedback', ['status'])
        op.create_index('ix_feedback_created_at', 'feedback', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_feedback_created_at', table_name='feedback')
    op.drop_index('ix_feedback_status', table_name='feedback')
    op.drop_index('ix_feedback_user_id', table_name='feedback')
    op.drop_table('feedback')
