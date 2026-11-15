import os
import logging
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import event, text
import redis

if not os.getenv('DOCKER_ENV'):
    load_dotenv()

from app.config import config_by_name, ProductionConfig
from app.extensions import db, bcrypt, jwt, migrate

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)


def create_app(config_name=None, test_config=None):
    """Application factory for AbyFlow."""
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_prefix=1)

    if config_name is None:
        env = os.getenv('FLASK_ENV', os.getenv('APP_ENV', 'development')).lower()
        config_name = env

    config_class = config_by_name.get(config_name, config_by_name['default'])
    app.config.from_object(config_class)
    app.config['APP_ENV'] = config_name

    if test_config:
        if isinstance(test_config, dict):
            app.config.update(test_config)
        else:
            app.config.from_object(test_config)

    if config_name == 'production' and not app.config.get('TESTING'):
        ProductionConfig.validate()

    db.init_app(app)
    # Enable SQLite foreign-key enforcement on every connection.
    with app.app_context():
        if db.engine.dialect.name == 'sqlite':

            @event.listens_for(db.engine, 'connect')
            def enable_sqlite_foreign_keys(
                dbapi_connection,
                connection_record
            ):
                cursor = dbapi_connection.cursor()
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.close()
    bcrypt.init_app(app)
    jwt.init_app(app)
    migrate.init_app(app, db)

    from app import models

    @app.route('/health', methods=['GET'])
    def health_check():
        status_data = {
            'status': 'healthy',
            'database': 'connected',
            'redis': 'connected'
        }
        status_code = 200

        try:
            db.session.execute(text('SELECT 1'))
        except Exception as e:
            logger.warning("Health check: database connectivity failed: %s", str(e))
            status_data['database'] = 'disconnected'
            status_data['status'] = 'degraded'
            status_code = 503

        try:
            redis_url = app.config.get('REDIS_URL', 'redis://localhost:6379/0')
            r = redis.from_url(redis_url, socket_timeout=2)
            r.ping()
        except Exception as e:
            logger.warning("Health check: redis connectivity failed: %s", str(e))
            status_data['redis'] = 'disconnected'
            status_data['status'] = 'degraded'
            status_code = 503

        return jsonify(status_data), status_code

    @app.route('/', methods=['GET'])
    def root():
        return render_template('index.html')

    from app.routes.auth import auth_bp
    from app.routes.subscriptions import sub_bp
    from app.routes.webhooks import webhook_bp
    from app.routes.tenants import tenant_bp
    from app.routes.customers import customer_bp
    from app.routes.invoices import invoice_bp
    from app.routes.operations import operations_bp
    from app.routes.ai import ai_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(sub_bp, url_prefix='/api/subscriptions')
    app.register_blueprint(webhook_bp, url_prefix='/api/webhooks')
    app.register_blueprint(tenant_bp, url_prefix='/api/tenants')
    app.register_blueprint(customer_bp, url_prefix='/api/customers')
    app.register_blueprint(invoice_bp, url_prefix='/api/invoices')
    app.register_blueprint(operations_bp, url_prefix='/api/ops')
    app.register_blueprint(ai_bp, url_prefix='/api/ai')

    return app
