import pytest
from app import create_app
from app.extensions import db as _db
from app.models import User


@pytest.fixture(scope='function')
def app():
    """Create and configure a Flask app for testing."""
    test_app = create_app('testing')

    with test_app.app_context():
        _db.create_all()
        yield test_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(scope='function')
def client(app):
    """Test client fixture."""
    return app.test_client()


@pytest.fixture(scope='function')
def db(app):
    """Database fixture for tests."""
    return _db


@pytest.fixture(scope='function')
def registered_user(app, db):
    """Create a test user and return the user object."""
    from app.extensions import bcrypt
    user = User(
        email='testuser@example.com',
        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
        plan_tier='free'
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture(scope='function')
def auth_headers(client, registered_user):
    """Obtain a valid JWT authorization header for the test user."""
    response = client.post(
        '/api/auth/login',
        json={'email': registered_user.email, 'password': 'Password123!'}
    )
    token = response.get_json()['token']
    return {'Authorization': f'Bearer {token}'}