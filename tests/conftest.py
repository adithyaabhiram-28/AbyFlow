import pytest
from app import create_app
from app.extentions import db as _db

@pytest.fixture(scope='function')
def app():
    app = create_app()
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": 'sqlite:///:memory:',
        "JWT_SECRET_KEY": 'test-secret-key'
    })

    with app.app_context():
        _db.create_all()
        yield app
        _db.drop_all()

@pytest.fixture(scope='function')
def client(app):
    return app.test_client()

def db(app):
    return _db