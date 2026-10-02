"""remember where a user moved an exercise on the Custom workout page

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # The app's startup schema check may have created it already.
    if 'exercise_group_choices' not in set(inspector.get_table_names()):
        op.create_table(
            'exercise_group_choices',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('exercise_key', sa.String(length=160), nullable=False),
            sa.Column('group_name', sa.String(length=32), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_exercise_group_choices_user_id', 'exercise_group_choices', ['user_id'])
        op.create_index(
            'idx_exercise_group_choice_user_exercise',
            'exercise_group_choices',
            ['user_id', 'exercise_key'],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index('idx_exercise_group_choice_user_exercise', table_name='exercise_group_choices')
    op.drop_index('ix_exercise_group_choices_user_id', table_name='exercise_group_choices')
    op.drop_table('exercise_group_choices')
