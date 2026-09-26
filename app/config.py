import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv('SECRET_KEY', os.getenv('JWT_SECRET_KEY', 'abyflow-dev-secret-key'))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    
    # JWT
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'abyflow-jwt-dev-secret-key')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=int(os.getenv('JWT_ACCESS_TOKEN_EXPIRES_HOURS', '24')))
    
    # Stripe
    STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
    
    # Celery & Redis
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = None
    
    # Application Base URL
    APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5000')


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False
    if not os.getenv('DATABASE_URL'):
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'abyflow.db')


class TestingConfig(Config):
    """Testing configuration with isolated in-memory database."""
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    JWT_SECRET_KEY = 'test-secret-key-do-not-use-in-production'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)
    STRIPE_PUBLIC_KEY = 'pk_test_dummy_key_for_testing'
    STRIPE_SECRET_KEY = 'sk_test_dummy_key_for_testing'
    STRIPE_WEBHOOK_SECRET = 'whsec_dummy_secret_for_testing'
    APP_BASE_URL = 'http://localhost:5000'


class ProductionConfig(Config):
    """Production configuration enforcing required environment variables."""
    DEBUG = False
    TESTING = False
    
    @classmethod
    def validate(cls):
        required_vars = [
            'DATABASE_URL',
            'JWT_SECRET_KEY',
            'STRIPE_SECRET_KEY',
            'STRIPE_WEBHOOK_SECRET',
        ]
        missing = [var for var in required_vars if not os.getenv(var)]
        if missing:
            raise ValueError(f"Missing required production environment variables: {', '.join(missing)}")


config_by_name = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
