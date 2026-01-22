"""
Main Flask application for the Workout Tracker.
"""

import os
import time

from sqlalchemy.exc import OperationalError

from config import Config
from models import initialize_database
from utils.logger import logger
from wait_for_db import wait_for_db
from workout_tracker import create_app


app = create_app(init_db=False)


# Application entry point
if __name__ == '__main__':
    try:
        init_retries = int(os.environ.get("DB_INIT_RETRIES", "5"))
        init_delay = float(os.environ.get("DB_INIT_DELAY", "3"))
        wait_timeout = int(os.environ.get("DB_WAIT_TIMEOUT", "120"))
        wait_interval = float(os.environ.get("DB_WAIT_INTERVAL", "2"))

        for attempt in range(1, init_retries + 1):
            try:
                wait_for_db(wait_timeout, wait_interval)
                initialize_database()
                logger.info("Database initialized successfully")
                break
            except OperationalError as exc:
                logger.warning(
                    "Database not ready (attempt %s/%s): %s",
                    attempt,
                    init_retries,
                    exc,
                    exc_info=True,
                )
                if attempt == init_retries:
                    raise
                time.sleep(init_delay)

        port = Config.PORT
        host = Config.HOST
        debug = Config.DEBUG

        logger.info(f"Starting Workout Tracker on {host}:{port} (debug={debug})")
        app.run(host=host, port=port, debug=debug, threaded=True)
    except Exception as e:
        logger.critical(f"Failed to start application: {e}", exc_info=True)
        raise
