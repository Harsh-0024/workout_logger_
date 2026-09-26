"""store the admin-uploaded app icon in the database so every host serves it

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # The app's startup schema check may have created it already.
    if 'app_icon' not in set(inspector.get_table_names()):
        op.create_table(
            'app_icon',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('image', sa.LargeBinary(), nullable=False),
            sa.Column('version', sa.String(length=16), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('app_icon')
