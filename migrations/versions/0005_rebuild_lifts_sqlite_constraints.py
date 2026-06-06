"""rebuild sqlite lifts constraints after startup migration

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-06
"""

from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != 'sqlite':
        return

    op.execute('DROP TABLE IF EXISTS _alembic_tmp_lifts')
    op.execute('DROP INDEX IF EXISTS ix_lifts_best_log_id')
    op.execute('DROP INDEX IF EXISTS ix_lifts_exercise')
    op.execute('DROP INDEX IF EXISTS idx_user_exercise')
    op.execute(
        """
        CREATE TABLE _alembic_tmp_lifts (
            id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            exercise VARCHAR(100) NOT NULL,
            best_log_id INTEGER,
            PRIMARY KEY (id),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_lifts_best_log_id_workout_logs
                FOREIGN KEY(best_log_id) REFERENCES workout_logs (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO _alembic_tmp_lifts (id, user_id, exercise, best_log_id)
        SELECT id, user_id, exercise, best_log_id
        FROM lifts
        """
    )
    op.execute('DROP TABLE lifts')
    op.execute('ALTER TABLE _alembic_tmp_lifts RENAME TO lifts')
    op.execute('CREATE INDEX ix_lifts_exercise ON lifts (exercise)')
    op.execute('CREATE INDEX ix_lifts_best_log_id ON lifts (best_log_id)')
    op.execute('CREATE INDEX idx_user_exercise ON lifts (user_id, exercise)')


def downgrade() -> None:
    pass
