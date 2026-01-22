"""Wait for database to become available before running migrations."""

import os
import time

from sqlalchemy import create_engine, text

from config import Config


def wait_for_db(timeout_seconds: int = 60, interval_seconds: float = 2.0) -> None:
    database_url = Config.get_database_url()
    if database_url.startswith("sqlite"):
        return

    start = time.time()
    last_error: Exception | None = None

    while time.time() - start < timeout_seconds:
        try:
            engine = create_engine(database_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_error = exc
            time.sleep(interval_seconds)

    raise RuntimeError(
        f"Database not ready after {timeout_seconds}s: {last_error}"
    )


if __name__ == "__main__":
    timeout = int(os.environ.get("DB_WAIT_TIMEOUT", "60"))
    interval = float(os.environ.get("DB_WAIT_INTERVAL", "2"))
    wait_for_db(timeout, interval)
