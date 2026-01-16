"""
Configuration management for the Workout Tracker application.
"""
import os
from typing import Optional


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
