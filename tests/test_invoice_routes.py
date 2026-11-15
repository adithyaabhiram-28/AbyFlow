
import pytest

from app.models import Invoice, InvoiceItem, User, TenantMembership
from app.extensions import bcrypt


@pytest.fixture
def tenant_headers(auth_headers, registered_user):
    """Add the authenticated user's personal tenant context."""
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
    from app.models import Tenant

    user = User(
        email='invoice-seconduser@example.com',
        password_hash=bcrypt.generate_password_hash(
            'Password123!'
        ).decode('utf-8'),
        plan_tier='free'
    )
    db.session.add(user)
    db.session.flush()

    tenant = Tenant(
        name='Invoice Second Workspace',
        slug=f'invoice-second-user-{user.id}'
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

    assert response.status_code == 200

    return {
        'Authorization': f"Bearer {response.get_json()['token']}",
        'X-Tenant-ID': str(second_tenant['tenant'].id),
    }


@pytest.fixture
def sample_customer(client, tenant_headers):
    """Create a customer for invoice testing."""
    response = client.post(
        '/api/customers',
        headers=tenant_headers,
        json={
            'name': 'Alice Johnson',
            'email': 'invoice-alice@example.com',
            'company_name': 'Acme Inc',
            'phone': '+1-555-0100',
        }
    )

    assert response.status_code == 201
    return response.get_json()['customer']


@pytest.fixture
def sample_invoice(client, tenant_headers, sample_customer):
    """Create a draft invoice through the API."""
    response = client.post(
        '/api/invoices',
        headers=tenant_headers,
        json={
            'customer_id': sample_customer['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'tax_rate': '18.00',
            'notes': 'Test invoice',
            'items': [
                {
                    'description': 'Backend development',
                    'quantity': '10',
                    'unit_price': '49.99',
                },
                {
                    'description': 'API integration',
                    'quantity': '5',
                    'unit_price': '29.99',
                },
            ],
        }
    )

    assert response.status_code == 201
    return response.get_json()['invoice']


# --------------------------------------------------
# CREATE INVOICE
# --------------------------------------------------

def test_create_invoice(client, tenant_headers, sample_customer):
    response = client.post(
        '/api/invoices',
        headers=tenant_headers,
        json={
            'customer_id': sample_customer['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'tax_rate': '18.00',
            'items': [
                {
                    'description': 'Backend development',
                    'quantity': '10',
                    'unit_price': '49.99',
                }
            ],
        }
    )

    assert response.status_code == 201

    data = response.get_json()
    invoice = data['invoice']

    assert data['message'] == 'Invoice created successfully'
    assert invoice['customer_id'] == sample_customer['id']
    assert invoice['status'] == 'draft'
    assert invoice['currency'] == 'USD'
    assert invoice['invoice_number'] == 'INV-000001'
    assert invoice['subtotal'] == '499.90'
    assert invoice['tax_rate'] == '18.00'
    assert invoice['tax_amount'] == '89.98'
    assert invoice['total'] == '589.88'
    assert len(invoice['items']) == 1


def test_create_invoice_rejects_missing_fields(
    client, tenant_headers
):
    response = client.post(
        '/api/invoices',
        headers=tenant_headers,
        json={}
    )

    assert response.status_code == 400


def test_create_invoice_rejects_invalid_customer(
    client, tenant_headers
):
    response = client.post(
        '/api/invoices',
        headers=tenant_headers,
        json={
            'customer_id': 99999,
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'items': [
                {
                    'description': 'Service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
        }
    )

    assert response.status_code == 404


def test_create_invoice_rejects_invalid_due_date(
    client, tenant_headers, sample_customer
):
    response = client.post(
        '/api/invoices',
        headers=tenant_headers,
        json={
            'customer_id': sample_customer['id'],
            'issue_date': '2026-12-08',
            'due_date': '2026-11-08',
            'items': [
                {
                    'description': 'Service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
        }
    )

    assert response.status_code == 400


def test_create_invoice_rejects_unsupported_fields(
    client, tenant_headers, sample_customer
):
    response = client.post(
        '/api/invoices',
        headers=tenant_headers,
        json={
            'customer_id': sample_customer['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'items': [
                {
                    'description': 'Service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
            'tenant_id': 999,
        }
    )

    assert response.status_code == 400


# --------------------------------------------------
# GET INVOICE
# --------------------------------------------------

def test_get_invoice(
    client, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    response = client.get(
        f'/api/invoices/{invoice_id}',
        headers=tenant_headers
    )

    assert response.status_code == 200

    invoice = response.get_json()['invoice']

    assert invoice['id'] == invoice_id
    assert invoice['invoice_number'] == 'INV-000001'
    assert invoice['status'] == 'draft'
    assert len(invoice['items']) == 2


def test_get_nonexistent_invoice(client, tenant_headers):
    response = client.get(
        '/api/invoices/99999',
        headers=tenant_headers
    )

    assert response.status_code == 404


def test_get_another_tenants_invoice(
    client,
    tenant_headers,
    second_tenant_headers,
    second_tenant,
    sample_invoice,
):
    customer_response = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Other Tenant Customer'}
    )

    assert customer_response.status_code == 201

    invoice_response = client.post(
        '/api/invoices',
        headers=second_tenant_headers,
        json={
            'customer_id': customer_response.get_json()['customer']['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'items': [
                {
                    'description': 'Private service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
        }
    )

    assert invoice_response.status_code == 201

    other_invoice_id = invoice_response.get_json()['invoice']['id']

    response = client.get(
        f'/api/invoices/{other_invoice_id}',
        headers=tenant_headers
    )

    assert response.status_code == 404


# --------------------------------------------------
# UPDATE INVOICE
# --------------------------------------------------

def test_update_invoice_notes(
    client, tenant_headers, sample_invoice
):
    response = client.patch(
        f"/api/invoices/{sample_invoice['id']}",
        headers=tenant_headers,
        json={'notes': 'Updated invoice notes'}
    )

    assert response.status_code == 200

    invoice = response.get_json()['invoice']
    assert invoice['notes'] == 'Updated invoice notes'


def test_update_invoice_items(
    client, tenant_headers, sample_invoice
):
    response = client.patch(
        f"/api/invoices/{sample_invoice['id']}",
        headers=tenant_headers,
        json={
            'items': [
                {
                    'description': 'Updated backend service',
                    'quantity': '2',
                    'unit_price': '150.00',
                }
            ]
        }
    )

    assert response.status_code == 200

    invoice = response.get_json()['invoice']

    assert len(invoice['items']) == 1
    assert invoice['items'][0]['description'] == (
        'Updated backend service'
    )
    assert invoice['subtotal'] == '300.00'
    assert invoice['tax_rate'] == '18.00'
    assert invoice['tax_amount'] == '54.00'
    assert invoice['total'] == '354.00'


def test_update_invoice_rejects_empty_payload(
    client, tenant_headers, sample_invoice
):
    response = client.patch(
        f"/api/invoices/{sample_invoice['id']}",
        headers=tenant_headers,
        json={}
    )

    assert response.status_code == 400


def test_update_invoice_rejects_invalid_tax_rate(
    client, tenant_headers, sample_invoice
):
    response = client.patch(
        f"/api/invoices/{sample_invoice['id']}",
        headers=tenant_headers,
        json={'tax_rate': '150.00'}
    )

    assert response.status_code == 400


def test_update_another_tenants_invoice(
    client,
    tenant_headers,
    second_tenant_headers,
    sample_invoice,
):
    other_customer = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Other Tenant Customer'}
    )

    assert other_customer.status_code == 201

    other_invoice = client.post(
        '/api/invoices',
        headers=second_tenant_headers,
        json={
            'customer_id': other_customer.get_json()['customer']['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'items': [
                {
                    'description': 'Private service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
        }
    )

    assert other_invoice.status_code == 201

    invoice_id = other_invoice.get_json()['invoice']['id']

    response = client.patch(
        f'/api/invoices/{invoice_id}',
        headers=tenant_headers,
        json={'notes': 'Unauthorized change'}
    )

    assert response.status_code == 404


# --------------------------------------------------
# DELETE INVOICE
# --------------------------------------------------

def test_owner_can_delete_draft_invoice(
    client, db, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    response = client.delete(
        f'/api/invoices/{invoice_id}',
        headers=tenant_headers
    )

    assert response.status_code == 200
    assert response.get_json()['message'] == (
        'Invoice deleted successfully'
    )

    assert db.session.get(Invoice, invoice_id) is None


def test_delete_nonexistent_invoice(client, tenant_headers):
    response = client.delete(
        '/api/invoices/99999',
        headers=tenant_headers
    )

    assert response.status_code == 404


def test_delete_another_tenants_invoice(
    client, tenant_headers, second_tenant_headers
):
    customer = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Other Tenant Customer'}
    )

    assert customer.status_code == 201

    created = client.post(
        '/api/invoices',
        headers=second_tenant_headers,
        json={
            'customer_id': customer.get_json()['customer']['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'items': [
                {
                    'description': 'Private service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
        }
    )

    assert created.status_code == 201
    invoice_id = created.get_json()['invoice']['id']

    response = client.delete(
        f'/api/invoices/{invoice_id}',
        headers=tenant_headers
    )

    assert response.status_code == 404


# --------------------------------------------------
# ISSUE INVOICE
# --------------------------------------------------

def test_issue_draft_invoice(
    client, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data['message'] == 'Invoice issued successfully'
    assert data['invoice']['id'] == invoice_id
    assert data['invoice']['status'] == 'issued'


def test_issue_invoice_twice(
    client, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    first_response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert first_response.status_code == 200

    second_response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert second_response.status_code == 409


def test_issue_another_tenants_invoice(
    client,
    tenant_headers,
    second_tenant_headers,
):
    customer = client.post(
        '/api/customers',
        headers=second_tenant_headers,
        json={'name': 'Private Customer'}
    )

    assert customer.status_code == 201

    created = client.post(
        '/api/invoices',
        headers=second_tenant_headers,
        json={
            'customer_id': customer.get_json()['customer']['id'],
            'issue_date': '2026-11-08',
            'due_date': '2026-12-08',
            'items': [
                {
                    'description': 'Private service',
                    'quantity': '1',
                    'unit_price': '100.00',
                }
            ],
        }
    )

    assert created.status_code == 201
    invoice_id = created.get_json()['invoice']['id']

    response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert response.status_code == 404


def test_issued_invoice_cannot_be_updated(
    client, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    issue_response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert issue_response.status_code == 200

    response = client.patch(
        f'/api/invoices/{invoice_id}',
        headers=tenant_headers,
        json={'notes': 'Attempted modification'}
    )

    assert response.status_code == 409


def test_issued_invoice_cannot_be_deleted(
    client, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    issue_response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert issue_response.status_code == 200

    response = client.delete(
        f'/api/invoices/{invoice_id}',
        headers=tenant_headers
    )

    assert response.status_code == 409


# --------------------------------------------------
# LIST INVOICES
# --------------------------------------------------

def test_list_invoices(
    client, tenant_headers, sample_invoice
):
    response = client.get(
        '/api/invoices',
        headers=tenant_headers
    )

    assert response.status_code == 200

    data = response.get_json()

    assert len(data['invoices']) == 1
    assert data['pagination']['total'] == 1
    assert data['pagination']['page'] == 1
    assert data['pagination']['per_page'] == 20
    assert data['invoices'][0]['id'] == sample_invoice['id']


def test_list_invoices_with_status_filter(
    client, tenant_headers, sample_invoice
):
    invoice_id = sample_invoice['id']

    issue_response = client.post(
        f'/api/invoices/{invoice_id}/issue',
        headers=tenant_headers
    )

    assert issue_response.status_code == 200

    response = client.get(
        '/api/invoices?status=issued',
        headers=tenant_headers
    )

    assert response.status_code == 200

    data = response.get_json()

    assert data['pagination']['total'] == 1
    assert data['invoices'][0]['status'] == 'issued'


def test_list_invoices_with_pagination(
    client, tenant_headers, sample_customer
):
    for index in range(3):
        response = client.post(
            '/api/invoices',
            headers=tenant_headers,
            json={
                'customer_id': sample_customer['id'],
                'issue_date': '2026-11-08',
                'due_date': '2026-12-08',
                'notes': f'Invoice {index}',
                'items': [
                    {
                        'description': 'Service',
                        'quantity': '1',
                        'unit_price': '100.00',
                    }
                ],
            }
        )

        assert response.status_code == 201

    response = client.get(
        '/api/invoices?page=1&per_page=2',
        headers=tenant_headers
    )

    assert response.status_code == 200

    data = response.get_json()

    assert len(data['invoices']) == 2
    assert data['pagination']['total'] == 3
    assert data['pagination']['pages'] == 2


def test_list_invoices_rejects_invalid_pagination(
    client, tenant_headers
):
    response = client.get(
        '/api/invoices?page=0',
        headers=tenant_headers
    )

    assert response.status_code == 400


def test_list_invoices_rejects_invalid_status(
    client, tenant_headers
):
    response = client.get(
        '/api/invoices?status=unknown',
        headers=tenant_headers
    )

    assert response.status_code == 400


# --------------------------------------------------
# TENANT AUTHORIZATION
# --------------------------------------------------

def test_invoice_routes_require_authentication(client):
    response = client.get('/api/invoices')

    assert response.status_code == 401


def test_invoice_routes_require_tenant_header(
    client, auth_headers
):
    response = client.get(
        '/api/invoices',
        headers=auth_headers
    )

    assert response.status_code == 400


def test_invoice_routes_reject_invalid_tenant_header(
    client, auth_headers
):
    headers = {
        **auth_headers,
        'X-Tenant-ID': 'invalid',
    }

    response = client.get(
        '/api/invoices',
        headers=headers
    )

    assert response.status_code == 400


def test_invoice_routes_reject_unowned_tenant(
    client, auth_headers
):
    headers = {
        **auth_headers,
        'X-Tenant-ID': '99999',
    }

    response = client.get(
        '/api/invoices',
        headers=headers
    )

    assert response.status_code == 403


def test_member_cannot_delete_invoice(
    client,
    db,
    tenant_headers,
    registered_user,
    sample_invoice,
):
    membership = TenantMembership.query.filter_by(
        user_id=registered_user.id,
        tenant_id=int(tenant_headers['X-Tenant-ID'])
    ).first()

    member = User(
        email='invoice-member@example.com',
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

    assert login.status_code == 200

    member_headers = {
        'Authorization': f"Bearer {login.get_json()['token']}",
        'X-Tenant-ID': str(membership.tenant_id),
    }

    response = client.delete(
        f"/api/invoices/{sample_invoice['id']}",
        headers=member_headers
    )

    assert response.status_code == 403
