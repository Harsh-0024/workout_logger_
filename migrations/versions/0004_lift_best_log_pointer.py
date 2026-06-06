"""convert lifts to best workout log pointers

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy_utils import JSONType
from sqlalchemy import inspect


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    lift_columns = {col['name'] for col in inspector.get_columns('lifts')}

    if op.get_bind().dialect.name == 'sqlite':
        op.execute('DROP TABLE IF EXISTS _alembic_tmp_lifts')
        op.execute('DROP INDEX IF EXISTS ix_lifts_best_log_id')

    with op.batch_alter_table('lifts') as batch_op:
        if 'best_log_id' not in lift_columns:
            batch_op.add_column(sa.Column('best_log_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_lifts_best_log_id_workout_logs',
                'workout_logs',
                ['best_log_id'],
                ['id'],
                ondelete='SET NULL',
            )
        batch_op.create_index('ix_lifts_best_log_id', ['best_log_id'])
        if 'best_string' in lift_columns:
            batch_op.drop_column('best_string')
        if 'sets_json' in lift_columns:
            batch_op.drop_column('sets_json')
        if 'updated_at' in lift_columns:
            batch_op.drop_column('updated_at')


def downgrade() -> None:
    with op.batch_alter_table('lifts') as batch_op:
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('sets_json', JSONType(), nullable=True))
        batch_op.add_column(sa.Column('best_string', sa.Text(), nullable=True))
        batch_op.drop_index('ix_lifts_best_log_id')
        batch_op.drop_constraint('fk_lifts_best_log_id_workout_logs', type_='foreignkey')
        batch_op.drop_column('best_log_id')
