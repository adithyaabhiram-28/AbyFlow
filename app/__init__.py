import os
import logging
from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy import text
import redis

from app.config import config_by_name, ProductionConfig
from app.extensions import db, bcrypt, jwt

# Load environment variables from .env if present
if not os.getenv('DOCKER_ENV'):
    load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)


def create_app(config_name=None, test_config=None):
    """Application factory for AbyFlow."""
    app = Flask(__name__)

    # Select configuration
    if config_name is None:
        env = os.getenv('FLASK_ENV', os.getenv('APP_ENV', 'development')).lower()
        config_name = env

    config_class = config_by_name.get(config_name, config_by_name['default'])
    app.config.from_object(config_class)

    # Apply test or explicit override configuration if provided
    if test_config:
        if isinstance(test_config, dict):
            app.config.update(test_config)
        else:
            app.config.from_object(test_config)

    # Validate production environment if running in production
    if config_name == 'production' and not app.config.get('TESTING'):
        ProductionConfig.validate()

    # Initialize extensions
    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)

    # Health check endpoint
    @app.route('/health', methods=['GET'])
    def health_check():
        health_status = {
            'status': 'healthy',
            'database': 'connected',
            'redis': 'connected'
        }
        status_code = 200

        # Check PostgreSQL database connectivity
        try:
            db.session.execute(text('SELECT 1'))
        except Exception as e:
            logger.warning("Health check: database connectivity failed: %s", str(e))
            health_status['database'] = 'disconnected'
            health_status['status'] = 'degraded'
            status_code = 503

        # Check Redis connectivity
        try:
            redis_url = app.config.get('REDIS_URL', 'redis://localhost:6379/0')
            r = redis.from_url(redis_url, socket_timeout=2)
            r.ping()
        except Exception as e:
            logger.warning("Health check: redis connectivity failed: %s", str(e))
            health_status['redis'] = 'disconnected'
            health_status['status'] = 'degraded'
            status_code = 503

        return jsonify(health_status), status_code

    # Root endpoint
    @app.route('/', methods=['GET'])
    def root():
        return jsonify({
            'name': 'AbyFlow API',
            'version': '1.0.0',
            'status': 'running'
        }), 200

    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.subscriptions import sub_bp
    from app.routes.webhooks import webhook_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(sub_bp, url_prefix='/api/subscriptions')
    app.register_blueprint(webhook_bp, url_prefix='/api/webhooks')

    return app