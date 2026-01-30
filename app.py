import os
import threading
import time

from flask import jsonify, request
from sqlalchemy.exc import OperationalError

from config import Config
from models import initialize_database
from utils.logger import logger
from wait_for_db import wait_for_db
from workout_tracker import create_app


logger.info("Importing app module (Flask app factory).")
app = create_app(init_db=False)


@app.route('/health')
def health_check():
    return (
        jsonify(
            {
                "status": "ok",
                "db_ready": bool(app.config.get("DB_READY")),
                "db_init_last_error": app.config.get("DB_INIT_LAST_ERROR"),
            }
        ),
        200,
    )


@app.before_request
def require_db_ready_for_requests():
    if request.path == '/health':
        return None
    if request.path.startswith('/static/'):
        return None
    if not app.config.get("DB_READY", False):
        if request.path in ('/', '/favicon.ico'):
            return "Service warming up", 200
        return "Service warming up", 503


# Application entry point
if __name__ == '__main__':
    try:
        logger.info("Starting app.py __main__ boot sequence.")
        app.config["DB_READY"] = False
        init_retries = int(os.environ.get("DB_INIT_RETRIES", "5"))
        init_delay = float(os.environ.get("DB_INIT_DELAY", "3"))
        wait_timeout = int(os.environ.get("DB_WAIT_TIMEOUT", "120"))
        wait_interval = float(os.environ.get("DB_WAIT_INTERVAL", "2"))

        def _init_db_in_background():
            attempt = 0
            while True:
                attempt += 1
                if init_retries > 0 and attempt > init_retries:
                    logger.critical("Database initialization failed after all retries")
                    return
                try:
                    wait_for_db(wait_timeout, wait_interval)
                    initialize_database()
                    app.config["DB_READY"] = True
                    app.config.pop("DB_INIT_LAST_ERROR", None)
                    logger.info("Database initialized successfully")
                    return
                except OperationalError as exc:
                    app.config["DB_INIT_LAST_ERROR"] = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "Database not ready (attempt %s/%s): %s",
                        attempt,
                        init_retries,
                        exc,
                        exc_info=True,
                    )
                    time.sleep(init_delay)
                except Exception as exc:
                    app.config["DB_INIT_LAST_ERROR"] = f"{type(exc).__name__}: {exc}"
                    logger.critical(
                        "Database initialization failed (attempt %s/%s): %s",
                        attempt,
                        init_retries,
                        exc,
                        exc_info=True,
                    )
                    time.sleep(init_delay)

        threading.Thread(target=_init_db_in_background, daemon=True).start()

        port = Config.PORT
        host = Config.HOST
        debug = Config.DEBUG

        logger.info(f"Starting Workout Tracker on {host}:{port} (debug={debug})")
        app.run(host=host, port=port, debug=debug, threaded=True)
    except Exception as e:
        logger.critical(f"Failed to start application: {e}", exc_info=True)
        raise
