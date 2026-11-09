import pytest
from app import create_app
from app.extensions import db as _db
from app.models import User, Tenant, TenantMembership


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
    """Create a test user with a personal workspace and owner membership."""
    from app.extensions import bcrypt

    user = User(
        email='testuser@example.com',
        password_hash=bcrypt.generate_password_hash(
            'Password123!'
        ).decode('utf-8'),
        plan_tier='free'
    )

    db.session.add(user)
    db.session.flush()

    tenant = Tenant(
        name='Personal Workspace',
        slug=f'personal-user-{user.id}'
    )

    db.session.add(tenant)
    db.session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role='owner'
    )

    db.session.add(membership)
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


@pytest.fixture
def registered_customer(db, registered_user):
    """Create a customer in the registered user's workspace."""
    from app.models import Customer, TenantMembership

    membership = TenantMembership.query.filter_by(
        user_id=registered_user.id
    ).first()

    customer = Customer(
        tenant_id=membership.tenant_id,
        name="Acme Corporation",
        email="billing@acme.com",
        company_name="Acme Inc.",
        phone="+15551234567",
    )

    db.session.add(customer)
    db.session.commit()

    return customer


@pytest.fixture
def second_tenant(db, registered_user):
    """Create a second tenant with a separate customer."""
    from app.models import Customer, TenantMembership

    tenant = Tenant(
        name="Second Workspace",
        slug="second-test-workspace",
    )

    db.session.add(tenant)
    db.session.flush()

    membership = TenantMembership(
        user_id=registered_user.id,
        tenant_id=tenant.id,
        role="owner",
    )

    customer = Customer(
        tenant_id=tenant.id,
        name="Other Corporation",
        email="billing@other.com",
        company_name="Other Inc.",
    )

    db.session.add_all([membership, customer])
    db.session.commit()

    return {
        "tenant": tenant,
        "customer": customer,
    }
