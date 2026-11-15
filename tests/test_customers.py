
import pytest

from app.models import User, Tenant, TenantMembership, Customer
from app.extensions import bcrypt


@pytest.fixture
def tenant_headers(auth_headers, registered_user):
    """Add the authenticated user's personal tenant context."""
    from app.models import TenantMembership

    membership = TenantMembership.query.filter_by(
        user_id=registered_user.id,
        role='owner'
    ).first()

    return {
        **auth_headers,
        'X-Tenant-ID': str(membership.tenant_id),
    }


@pytest.fixture
def second_tenant(db):
    """Create another user with an independent tenant."""
    user = User(
        email='seconduser@example.com',
        password_hash=bcrypt.generate_password_hash(
            'Password123!'
        ).decode('utf-8'),
        plan_tier='free'
    )
    db.session.add(user)
    db.session.flush()

    tenant = Tenant(
        name='Second Workspace',
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

    return {'user': user, 'tenant': tenant}


@pytest.fixture
def second_tenant_headers(client, second_tenant):
    """Authenticate the second user and select their tenant."""
    response = client.post(
        '/api/auth/login',
        json={
            'email': second_tenant['user'].email,
            'password': 'Password123!'
        }
    )

    token = response.get_json()['token']

    return {
        'Authorization': f'Bearer {token}',
        'X-Tenant-ID': str(second_tenant['tenant'].id),
    }


@pytest.fixture
def sample_customer(client, tenant_headers):
    """Create a customer for the authenticated tenant."""
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice Johnson',
            'email': 'alice@example.com',
            'company_name': 'Acme Inc',
            'phone': '+1-555-0100',
        }
    )

    assert response.status_code == 201
    return response.get_json()['customer']


# --------------------------------------------------
# CREATE CUSTOMER
# --------------------------------------------------

def test_create_customer(client, tenant_headers):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice Johnson',
            'email': 'alice@example.com',
            'company_name': 'Acme Inc',
            'phone': '+1-555-0100',
        }
    )

    assert response.status_code == 201

    data = response.get_json()

    assert data['message'] == 'Customer created successfully'
    assert data['customer']['name'] == 'Alice Johnson'
    assert data['customer']['email'] == 'alice@example.com'
    assert data['customer']['company_name'] == 'Acme Inc'
    assert data['customer']['phone'] == '+1-555-0100'
    assert data['customer']['tenant_id'] == int(
        tenant_headers['X-Tenant-ID']
    )


def test_create_customer_requires_name(client, tenant_headers):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={'email': 'alice@example.com'}
    )

    assert response.status_code == 400


def test_create_customer_rejects_invalid_email(client, tenant_headers):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice',
            'email': 'invalid-email',
        }
    )

    assert response.status_code == 400


def test_create_customer_rejects_unsupported_fields(
    client, tenant_headers
):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice',
            'is_admin': True,
        }
    )

    assert response.status_code == 400


def test_create_customer_normalizes_email(client, tenant_headers):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice',
            'email': '  ALICE@EXAMPLE.COM  ',
        }
    )

    assert response.status_code == 201
    assert response.get_json()['customer']['email'] == (
        'alice@example.com'
    )


def test_duplicate_customer_email_same_tenant(
    client, tenant_headers, sample_customer
):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Another Alice',
            'email': 'alice@example.com',
        }
    )

    assert response.status_code == 409


def test_same_email_allowed_in_different_tenants(
    client, tenant_headers, second_tenant_headers, sample_customer
):
    response = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={
            'name': 'Alice from another workspace',
            'email': 'alice@example.com',
        }
    )

    assert response.status_code == 201


# --------------------------------------------------
# LIST CUSTOMERS
# --------------------------------------------------

def test_list_customers(client, tenant_headers, sample_customer):
    response = client.get(
        '/api/customers',
        headers=tenant_headers
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data['count'] == 1
    assert len(data['customers']) == 1
    assert data['customers'][0]['name'] == 'Alice Johnson'


def test_list_customers_does_not_expose_other_tenant(
    client, tenant_headers, second_tenant_headers, sample_customer
):
    client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Other Tenant Customer'}
    )

    response = client.get(
        '/api/customers',
        headers=tenant_headers
    )

    assert response.status_code == 200
    assert response.get_json()['count'] == 1


# --------------------------------------------------
# GET CUSTOMER
# --------------------------------------------------

def test_get_customer(client, tenant_headers, sample_customer):
    customer_id = sample_customer['id']

    response = client.get(
        f'/api/customers/{customer_id}',
        headers=tenant_headers
    )

    assert response.status_code == 200
    assert response.get_json()['customer']['id'] == customer_id


def test_get_nonexistent_customer(client, tenant_headers):
    response = client.get(
        '/api/customers/99999',
        headers=tenant_headers
    )

    assert response.status_code == 404


def test_get_another_tenants_customer(
    client, tenant_headers, second_tenant_headers
):
    created = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Private Customer'}
    )

    customer_id = created.get_json()['customer']['id']

    response = client.get(
        f'/api/customers/{customer_id}',
        headers=tenant_headers
    )

    assert response.status_code == 404


# --------------------------------------------------
# UPDATE CUSTOMER
# --------------------------------------------------

def test_update_customer(client, tenant_headers, sample_customer):
    customer_id = sample_customer['id']

    response = client.patch(
        f'/api/customers/{customer_id}',
        headers=tenant_headers,
        json={
            'name': 'Alice Updated',
            'company_name': 'Updated Company',
        }
    )

    assert response.status_code == 200

    data = response.get_json()['customer']

    assert data['name'] == 'Alice Updated'
    assert data['company_name'] == 'Updated Company'


def test_update_customer_email_normalization(
    client, tenant_headers, sample_customer
):
    customer_id = sample_customer['id']

    response = client.patch(
        f'/api/customers/{customer_id}',
        headers=tenant_headers,
        json={'email': '  NEW@EXAMPLE.COM  '}
    )

    assert response.status_code == 200
    assert response.get_json()['customer']['email'] == (
        'new@example.com'
    )


def test_update_customer_rejects_empty_data(
    client, tenant_headers, sample_customer
):
    response = client.patch(
        f"/api/customers/{sample_customer['id']}",
        headers=tenant_headers,
        json={}
    )

    assert response.status_code == 400


def test_update_customer_rejects_duplicate_email(
    client, tenant_headers
):
    first = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'First Customer',
            'email': 'first@example.com',
        }
    )

    second = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Second Customer',
            'email': 'second@example.com',
        }
    )

    second_id = second.get_json()['customer']['id']

    response = client.patch(
        f'/api/customers/{second_id}',
        headers=tenant_headers,
        json={'email': 'first@example.com'}
    )

    assert response.status_code == 409


def test_update_another_tenants_customer(
    client, tenant_headers, second_tenant_headers
):
    created = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Private Customer'}
    )

    customer_id = created.get_json()['customer']['id']

    response = client.patch(
        f'/api/customers/{customer_id}',
        headers=tenant_headers,
        json={'name': 'Unauthorized Update'}
    )

    assert response.status_code == 404


# --------------------------------------------------
# DELETE CUSTOMER
# --------------------------------------------------

def test_owner_can_delete_customer(
    client, db, tenant_headers, sample_customer
):
    customer_id = sample_customer['id']

    response = client.delete(
        f'/api/customers/{customer_id}',
        headers=tenant_headers
    )

    assert response.status_code == 200
    assert response.get_json()['message'] == (
        'Customer deleted successfully'
    )

    assert db.session.get(Customer, customer_id) is None


def test_delete_nonexistent_customer(client, tenant_headers):
    response = client.delete(
        '/api/customers/99999',
        headers=tenant_headers
    )

    assert response.status_code == 404


def test_member_cannot_delete_customer(
    client, db, tenant_headers, registered_user, sample_customer
):
    membership = TenantMembership.query.filter_by(
        user_id=registered_user.id,
        tenant_id=int(tenant_headers['X-Tenant-ID'])
    ).first()

    member = User(
        email='member@example.com',
        password_hash=bcrypt.generate_password_hash(
            'Password123!'
        ).decode('utf-8'),
        plan_tier='free'
    )

    db.session.add(member)
    db.session.flush()

    db.session.add(
        TenantMembership(
            user_id=member.id,
            tenant_id=membership.tenant_id,
            role='member'
        )
    )
    db.session.commit()

    login = client.post(
        '/api/auth/login',
        json={
            'email': member.email,
            'password': 'Password123!'
        }
    )

    member_headers = {
        'Authorization': f"Bearer {login.get_json()['token']}",
        'X-Tenant-ID': str(membership.tenant_id),
    }

    response = client.delete(
        f"/api/customers/{sample_customer['id']}",
        headers=member_headers
    )

    assert response.status_code == 403


# --------------------------------------------------
# TENANT AUTHORIZATION
# --------------------------------------------------

def test_customer_routes_require_authentication(client):
    response = client.get('/api/customers')
    assert response.status_code == 401


def test_customer_routes_require_tenant_header(client, auth_headers):
    response = client.get(
        '/api/customers',
        headers=auth_headers
    )

    assert response.status_code == 400


def test_customer_routes_reject_invalid_tenant_header(
    client, auth_headers
):
    headers = {
        **auth_headers,
        'X-Tenant-ID': 'invalid',
    }

    response = client.get(
        '/api/customers',
        headers=headers
    )

    assert response.status_code == 400


def test_customer_routes_reject_unowned_tenant(
    client, auth_headers
):
    headers = {
        **auth_headers,
        'X-Tenant-ID': '99999',
    }

    response = client.get(
        '/api/customers',
        headers=headers
    )

    assert response.status_code == 403

def test_create_customer_rejects_malformed_email(
    client, tenant_headers
):
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice',
            'email': 'alice@@example..com',
        }
    )

    assert response.status_code == 400


def test_update_customer_rejects_malformed_email(
    client, tenant_headers, sample_customer
):
    response = client.patch(
        f"/api/customers/{sample_customer['id']}",
        headers=tenant_headers,
        json={'email': 'not-an-email'}
    )

    assert response.status_code == 400