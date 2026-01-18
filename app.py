"""
Main Flask application for the Workout Tracker.
"""


from config import Config
from models import initialize_database
from utils.logger import logger
from workout_tracker import create_app


app = create_app()


# Application entry point
if __name__ == '__main__':
    try:
        initialize_database()
        logger.info("Database initialized successfully")
        
        port = Config.PORT
        host = Config.HOST
        debug = Config.DEBUG
        
        logger.info(f"Starting Workout Tracker on {host}:{port} (debug={debug})")
        app.run(host=host, port=port, debug=debug, threaded=True)
    except Exception as e:
        logger.critical(f"Failed to start application: {e}", exc_info=True)
        raise
