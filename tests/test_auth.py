from datetime import timedelta
import time
from flask_jwt_extended import create_access_token


def test_register_user_success(client):
    """Test successful user registration."""
    payload = {
        "email": "newuser@example.com",
        "password": "SecurePassword123!"
    }
    response = client.post('/api/auth/register', json=payload)
    assert response.status_code == 201
    data = response.get_json()
    assert data['message'] == 'User created successfully'
    assert data['email'] == 'newuser@example.com'


def test_register_duplicate_user(client):
    """Test registration with existing email returns 409."""
    payload = {
        "email": "dup@example.com",
        "password": "SecurePassword123!"
    }
    first_res = client.post('/api/auth/register', json=payload)
    assert first_res.status_code == 201

    second_res = client.post('/api/auth/register', json=payload)
    assert second_res.status_code == 409
    data = second_res.get_json()
    assert "already exists" in data["error"]


def test_register_email_normalization(client):
    """Test email is trimmed and lowercased upon registration."""
    payload = {
        "email": "  MiXeDCase@Example.com  ",
        "password": "SecurePassword123!"
    }
    res = client.post('/api/auth/register', json=payload)
    assert res.status_code == 201
    assert res.get_json()['email'] == 'mixedcase@example.com'

    # Duplicate with different casing
    dup_res = client.post('/api/auth/register', json={"email": "mixedcase@example.com", "password": "SecurePassword123!"})
    assert dup_res.status_code == 409


def test_register_missing_fields(client):
    """Test registration validation for missing or invalid fields."""
    # Empty body
    res = client.post('/api/auth/register', json={})
    assert res.status_code == 400

    # Missing password
    res = client.post('/api/auth/register', json={'email': 'test@example.com'})
    assert res.status_code == 400

    # Missing email
    res = client.post('/api/auth/register', json={'password': 'Password123!'})
    assert res.status_code == 400

    # Invalid email format
    res = client.post('/api/auth/register', json={'email': 'not-an-email', 'password': 'Password123!'})
    assert res.status_code == 400
    assert "Invalid email format" in res.get_json()['error']

    # Short password
    res = client.post('/api/auth/register', json={'email': 'valid@example.com', 'password': '123'})
    assert res.status_code == 400
    assert "at least 6 characters" in res.get_json()['error']


def test_login_success(client, registered_user):
    """Test successful login with correct credentials."""
    response = client.post('/api/auth/login', json={
        'email': registered_user.email,
        'password': 'Password123!'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == 'Login successful'
    assert 'token' in data
    assert len(data['token']) > 20


def test_login_invalid_credentials(client, registered_user):
    """Test login failure on invalid password or nonexistent email."""
    # Wrong password
    res = client.post('/api/auth/login', json={
        'email': registered_user.email,
        'password': 'WrongPassword!'
    })
    assert res.status_code == 401
    assert 'Invalid email or password' in res.get_json()['error']

    # Nonexistent user
    res = client.post('/api/auth/login', json={
        'email': 'nobody@example.com',
        'password': 'Password123!'
    })
    assert res.status_code == 401
    assert 'Invalid email or password' in res.get_json()['error']

    # Empty payload
    res = client.post('/api/auth/login', json={})
    assert res.status_code == 401


def test_dashboard_authenticated(client, auth_headers, registered_user):
    """Test authenticated dashboard returns user email and plan tier."""
    response = client.get('/api/auth/dashboard', headers=auth_headers)
    assert response.status_code == 200
    data = response.get_json()
    assert registered_user.email in data['message']
    assert data['plan_tier'] == 'free'
    assert data['email'] == registered_user.email


def test_dashboard_unauthenticated(client):
    """Test dashboard without token returns 401."""
    response = client.get('/api/auth/dashboard')
    assert response.status_code == 401


def test_dashboard_invalid_token(client):
    """Test dashboard with malformed token returns 401/422."""
    response = client.get('/api/auth/dashboard', headers={'Authorization': 'Bearer invalid_token_123'})
    assert response.status_code in (401, 422)


def test_dashboard_user_not_found(app, client, db, registered_user):
    """Test dashboard when user exists in JWT but deleted from database."""
    with app.app_context():
        token = create_access_token(identity='deleteduser@example.com')

    response = client.get('/api/auth/dashboard', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 404
    assert response.get_json()['error'] == 'User not found'


def test_jwt_token_expiration(app, client):
    """Test that JWT tokens obey expiration time."""
    with app.app_context():
        # Create token expiring in 1 second
        short_token = create_access_token(identity='testuser@example.com', expires_delta=timedelta(seconds=1))

    # Wait for token to expire
    time.sleep(1.2)

    response = client.get('/api/auth/dashboard', headers={'Authorization': f'Bearer {short_token}'})
    assert response.status_code == 401
