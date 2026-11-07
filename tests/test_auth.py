from datetime import timedelta
import time
from flask_jwt_extended import create_access_token
from app.models import User, Tenant, TenantMembership


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


def test_list_user_tenants(client, auth_headers, registered_user):
    """Authenticated user can list their own tenants."""
    response = client.get(
        '/api/tenants',
        headers=auth_headers
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data['count'] == 1
    assert len(data['tenants']) == 1

    tenant = data['tenants'][0]

    assert tenant['name'] == 'Personal Workspace'
    assert tenant['slug'] == f'personal-user-{registered_user.id}'
    assert tenant['role'] == 'owner'


def test_resolve_current_tenant(client, auth_headers, registered_user):
    """User can resolve a tenant they belong to."""
    membership = TenantMembership.query.filter_by(
        user_id=registered_user.id
    ).one()

    response = client.get(
        '/api/tenants/current',
        headers={
            **auth_headers,
            'X-Tenant-ID': str(membership.tenant_id)
        }
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data['tenant']['id'] == membership.tenant_id
    assert data['tenant']['name'] == 'Personal Workspace'
    assert data['membership']['role'] == 'owner'


def test_resolve_tenant_missing_header(client, auth_headers):
    """Tenant resolution requires an explicit tenant ID."""
    response = client.get(
        '/api/tenants/current',
        headers=auth_headers
    )

    assert response.status_code == 400
    assert 'X-Tenant-ID' in response.get_json()['error']


def test_resolve_tenant_invalid_header(client, auth_headers):
    """Malformed tenant IDs must be rejected."""
    response = client.get(
        '/api/tenants/current',
        headers={
            **auth_headers,
            'X-Tenant-ID': 'not-a-number'
        }
    )

    assert response.status_code == 400


def test_user_cannot_access_another_users_tenant(
    client, db, auth_headers, registered_user
):
    """A user cannot access a tenant without membership."""
    from app.extensions import bcrypt

    other_user = User(
        email='anotheruser@example.com',
        password_hash=bcrypt.generate_password_hash(
            'Password123!'
        ).decode('utf-8'),
        plan_tier='free'
    )

    db.session.add(other_user)
    db.session.flush()

    other_tenant = Tenant(
        name='Other Workspace',
        slug=f'personal-user-{other_user.id}'
    )

    db.session.add(other_tenant)
    db.session.flush()

    db.session.add(TenantMembership(
        user_id=other_user.id,
        tenant_id=other_tenant.id,
        role='owner'
    ))

    db.session.commit()

    response = client.get(
        '/api/tenants/current',
        headers={
            **auth_headers,
            'X-Tenant-ID': str(other_tenant.id)
        }
    )

    assert response.status_code == 403
    assert response.get_json()['error'] == (
        'You do not have access to this tenant'
    )


def test_register_creates_personal_workspace(client, db):
    """Registration creates a user, personal tenant, and owner membership."""
    response = client.post('/api/auth/register', json={
        'email': 'workspace@example.com',
        'password': 'SecurePassword123!'
    })

    assert response.status_code == 201

    user = User.query.filter_by(email='workspace@example.com').first()

    assert user is not None
    assert user.plan_tier == 'free'

    tenant = Tenant.query.filter_by(
        slug=f'personal-user-{user.id}'
    ).first()

    assert tenant is not None
    assert tenant.name == 'Personal Workspace'

    membership = TenantMembership.query.filter_by(
        user_id=user.id,
        tenant_id=tenant.id
    ).first()

    assert membership is not None
    assert membership.role == 'owner'


def test_register_duplicate_does_not_create_extra_workspace(client, db):
    """Duplicate registration must not create additional records."""
    payload = {
        'email': 'duplicate-workspace@example.com',
        'password': 'SecurePassword123!'
    }

    first_response = client.post('/api/auth/register', json=payload)
    assert first_response.status_code == 201

    assert User.query.count() == 1
    assert Tenant.query.count() == 1
    assert TenantMembership.query.count() == 1

    second_response = client.post('/api/auth/register', json=payload)
    assert second_response.status_code == 409

    assert User.query.count() == 1
    assert Tenant.query.count() == 1
    assert TenantMembership.query.count() == 1


def test_two_users_receive_separate_workspaces(client, db):
    """Different users must receive separate personal workspaces."""
    users = [
        'first-workspace@example.com',
        'second-workspace@example.com',
    ]

    for email in users:
        response = client.post('/api/auth/register', json={
            'email': email,
            'password': 'SecurePassword123!'
        })
        assert response.status_code == 201

    first_user = User.query.filter_by(email=users[0]).first()
    second_user = User.query.filter_by(email=users[1]).first()

    first_membership = TenantMembership.query.filter_by(
        user_id=first_user.id
    ).one()

    second_membership = TenantMembership.query.filter_by(
        user_id=second_user.id
    ).one()

    assert first_membership.role == 'owner'
    assert second_membership.role == 'owner'
    assert first_membership.tenant_id != second_membership.tenant_id

    assert User.query.count() == 2
    assert Tenant.query.count() == 2
    assert TenantMembership.query.count() == 2


def test_registration_rolls_back_if_membership_creation_fails(
    app, client, db, monkeypatch
):
    """A failed membership creation must roll back user and tenant."""
    import app.routes.auth as auth_module

    def fail_membership_creation(*args, **kwargs):
        raise RuntimeError('Simulated membership creation failure')

    monkeypatch.setattr(
        auth_module,
        'TenantMembership',
        fail_membership_creation
    )

    response = client.post('/api/auth/register', json={
        'email': 'rollback@example.com',
        'password': 'SecurePassword123!'
    })

    assert response.status_code == 500

    assert User.query.filter_by(
        email='rollback@example.com'
    ).first() is None

    assert User.query.count() == 0
    assert Tenant.query.count() == 0
    assert TenantMembership.query.count() == 0
