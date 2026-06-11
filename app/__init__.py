import os
from dotenv import load_dotenv
from flask import Flask
from app.extentions import db, bcrypt, jwt

load_dotenv()

def create_app():
    app = Flask(__name__)
    database_url = os.getenv('DATABASE_URL')
    if database_url and database_url.startswith(('postgresql://', 'postgres://')):
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'abyflow.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JWT_SECRET_KEY'] = 'super-secret-jwt-key-change-in-production'
    app.config['STRIPE_PUBLIC_KEY'] = os.getenv('STRIPE_PUBLISHABLE_KEY')
    app.config['STRIPE_SECRET_KEY'] = os.getenv('STRIPE_SECRET_KEY')

    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)

    from app.routes.auth import auth_bp
    from app.routes.subscriptions import sub_bp
    from app.routes.webhooks import webhook_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(sub_bp, url_prefix='/api/subscriptions')
    app.register_blueprint(webhook_bp, url_prefix='/api/webhooks')

    from app.models import User, ProcessedEvent
    
    # with app.app_context():
    #     db.create_all()

    return app