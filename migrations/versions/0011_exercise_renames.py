"""remember exercise renames, so logs under an old name reach the new one

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # The app's startup schema check may have created it already.
    if 'exercise_renames' not in set(inspector.get_table_names()):
        op.create_table(
            'exercise_renames',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('old_key', sa.String(length=160), nullable=False),
            sa.Column('old_name', sa.String(length=160), nullable=False),
            sa.Column('new_name', sa.String(length=160), nullable=False),
            sa.Column('log_ids', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_exercise_renames_user_id', 'exercise_renames', ['user_id'])
        op.create_index(
            'idx_exercise_rename_user_old',
            'exercise_renames',
            ['user_id', 'old_key'],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index('idx_exercise_rename_user_old', table_name='exercise_renames')
    op.drop_index('ix_exercise_renames_user_id', table_name='exercise_renames')
    op.drop_table('exercise_renames')
