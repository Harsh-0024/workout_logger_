"""
Configuration management for the Workout Tracker application.
"""
import os
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


class Config:
    """Base configuration class."""
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # Database
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # App Settings
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    PORT = int(os.environ.get('PORT', 5001))
    HOST = os.environ.get('HOST', '0.0.0.0')
    
    # Feature Flags
    ENABLE_CSRF = True
    ENABLE_RATE_LIMITING = False
    
    # Pagination
    ITEMS_PER_PAGE = 20
    
    # Timezone (IST)
    TIMEZONE_OFFSET_HOURS = 5
    TIMEZONE_OFFSET_MINUTES = 30
    
    # Email Configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = (
        os.environ.get('MAIL_DEFAULT_SENDER')
        or os.environ.get('MAIL_USERNAME')
        or 'noreply@workouttracker.com'
    )

    # Brevo Transactional Email API (recommended for Railway)
    BREVO_API_KEY = os.environ.get('BREVO_API_KEY')
    BREVO_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL') or MAIL_DEFAULT_SENDER
    BREVO_SENDER_NAME = os.environ.get('BREVO_SENDER_NAME', 'Workout Tracker')
    
    # Authentication
    REMEMBER_COOKIE_DURATION = 30  # days
    VERIFICATION_TOKEN_EXPIRY = 24  # hours
    OTP_TOKEN_EXPIRY_MINUTES = int(os.environ.get('OTP_TOKEN_EXPIRY_MINUTES', 10))

    # Admin bootstrap (create/update admin user on startup if password is provided)
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@workouttracker.local')
    ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', '')
    ADMIN_EMAIL_ALLOWLIST = {
        email.strip().lower()
        for email in ADMIN_EMAILS.split(',')
        if email.strip()
    }
    if ADMIN_EMAIL:
        ADMIN_EMAIL_ALLOWLIST.add(ADMIN_EMAIL.lower())
    
    @staticmethod
    def get_database_url() -> str:
        """Get database URL with proper formatting."""
        database_url = Config.DATABASE_URL
        if database_url and database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        if not database_url:
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            DB_PATH = os.path.join(BASE_DIR, "workout.db")
            database_url = f"sqlite:///{DB_PATH}"
        
        return database_url


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    ENABLE_CSRF = True
    ENABLE_RATE_LIMITING = True


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DATABASE_URL = 'sqlite:///:memory:'


# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
