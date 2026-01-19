import os
import urllib.parse
from datetime import datetime

from flask import Flask, render_template, send_from_directory
from flask_login import LoginManager
from flask_mail import Mail

from config import Config
from models import Session, User
from services.email_service import EmailService
from utils.logger import logger

from .routes.admin import register_admin_routes
from .routes.auth import register_auth_routes
from .routes.plans import register_plan_routes
from .routes.stats import register_stats_routes
from .routes.workouts import register_workout_routes


def create_app(config_object=Config):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static'),
    )
    app.secret_key = config_object.SECRET_KEY
    app.config.from_object(config_object)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return Session.query(User).get(int(user_id))
        except Exception:
            return None

    mail = Mail(app)
    email_service = EmailService(mail)

    register_auth_routes(app, email_service)
    register_admin_routes(app, email_service)
    register_workout_routes(app)
    register_stats_routes(app)
    register_plan_routes(app)

    @app.template_filter('url_encode')
    def url_encode_filter(s):
        return urllib.parse.quote(str(s))

    @app.template_filter('format_date')
    def format_date_filter(date_obj):
        if isinstance(date_obj, datetime):
            return date_obj.strftime('%Y-%m-%d')
        return str(date_obj)

    @app.route('/favicon.ico')
    def favicon():
        favicon_path = os.path.join(app.static_folder, 'favicon.ico')
        if os.path.exists(favicon_path):
            return send_from_directory(app.static_folder, 'favicon.ico')
        return '', 204

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        Session.remove()

    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', error_code=404, error_message="Page not found"), 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal server error: {error}", exc_info=True)
        Session.rollback()
        return (
            render_template('error.html', error_code=500, error_message="Internal server error"),
            500,
        )

    return app
