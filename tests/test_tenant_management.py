import pytest
from app.models import User, Tenant, TenantMembership
from app.extensions import bcrypt


@pytest.fixture
def second_user(app, db):
    """Create a second registered user with their own workspace."""
    user = User(
        email="seconduser@example.com",
        password_hash=bcrypt.generate_password_hash("Password123!").decode("utf-8"),
        plan_tier="free"
    )
    db.session.add(user)
    db.session.flush()

    tenant = Tenant(
        name="Second Workspace",
        slug=f"personal-user-{user.id}"
    )
    db.session.add(tenant)
    db.session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="owner"
    )
    db.session.add(membership)
    db.session.commit()
    return user


def test_create_workspace_success(client, auth_headers, registered_user):
    """Authenticated user can create a new workspace and becomes owner."""
    response = client.post(
        '/api/tenants',
        headers=auth_headers,
        json={'name': 'Acme Corporation', 'slug': 'acme-corp'}
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['message'] == 'Workspace created successfully'
    assert data['tenant']['name'] == 'Acme Corporation'
    assert data['tenant']['slug'] == 'acme-corp'
    assert data['tenant']['role'] == 'owner'

    # User now has 2 workspaces
    list_res = client.get('/api/tenants', headers=auth_headers)
    assert list_res.get_json()['count'] == 2


def test_create_workspace_validation(client, auth_headers):
    """Test invalid payloads when creating a workspace."""
    # Missing name
    res = client.post('/api/tenants', headers=auth_headers, json={})
    assert res.status_code == 400

    # Non-dict body
    res = client.post('/api/tenants', headers=auth_headers, data="invalid")
    assert res.status_code == 400


def test_create_workspace_duplicate_slug_rejected(client, auth_headers):
    """Creating a workspace with an existing slug returns 409."""
    res1 = client.post(
        '/api/tenants',
        headers=auth_headers,
        json={'name': 'First Workspace', 'slug': 'duplicate-slug'}
    )
    assert res1.status_code == 201

    res2 = client.post(
        '/api/tenants',
        headers=auth_headers,
        json={'name': 'Second Workspace', 'slug': 'duplicate-slug'}
    )
    assert res2.status_code == 409
    assert 'already exists' in res2.get_json()['error']


def test_list_workspace_members(client, auth_headers, registered_user):
    """Owner can list members in current workspace."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    response = client.get('/api/tenants/current/members', headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data['count'] == 1
    assert data['members'][0]['email'] == registered_user.email
    assert data['members'][0]['role'] == 'owner'


def test_add_workspace_member_success(client, auth_headers, registered_user, second_user):
    """Owner can add another existing user to their workspace."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    response = client.post(
        '/api/tenants/current/members',
        headers=headers,
        json={'email': second_user.email, 'role': 'member'}
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['member']['email'] == second_user.email
    assert data['member']['role'] == 'member'

    # Second user now has access to this workspace
    list_res = client.get('/api/tenants/current/members', headers=headers)
    assert list_res.get_json()['count'] == 2


def test_add_nonexistent_user_to_workspace_fails(client, auth_headers, registered_user):
    """Adding a nonexistent user fails with 400."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    response = client.post(
        '/api/tenants/current/members',
        headers=headers,
        json={'email': 'nobody@example.com', 'role': 'member'}
    )
    assert response.status_code == 400
    assert 'does not exist' in response.get_json()['error']


def test_add_duplicate_member_fails(client, auth_headers, registered_user, second_user):
    """Adding an already member user returns 409."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    client.post(
        '/api/tenants/current/members',
        headers=headers,
        json={'email': second_user.email, 'role': 'member'}
    )

    dup_res = client.post(
        '/api/tenants/current/members',
        headers=headers,
        json={'email': second_user.email, 'role': 'member'}
    )
    assert dup_res.status_code == 409
    assert 'already a member' in dup_res.get_json()['error']


def test_update_member_role(client, auth_headers, registered_user, second_user):
    """Owner can update a member's role."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    client.post(
        '/api/tenants/current/members',
        headers=headers,
        json={'email': second_user.email, 'role': 'member'}
    )

    update_res = client.patch(
        f'/api/tenants/current/members/{second_user.id}',
        headers=headers,
        json={'role': 'admin'}
    )
    assert update_res.status_code == 200
    assert update_res.get_json()['member']['role'] == 'admin'


def test_cannot_demote_last_owner(client, auth_headers, registered_user):
    """Cannot demote the only owner in the workspace."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    res = client.patch(
        f'/api/tenants/current/members/{registered_user.id}',
        headers=headers,
        json={'role': 'member'}
    )
    assert res.status_code == 400
    assert 'Cannot demote the last owner' in res.get_json()['error']


def test_remove_member_success(client, auth_headers, registered_user, second_user):
    """Owner can remove a member."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    client.post(
        '/api/tenants/current/members',
        headers=headers,
        json={'email': second_user.email, 'role': 'member'}
    )

    del_res = client.delete(
        f'/api/tenants/current/members/{second_user.id}',
        headers=headers
    )
    assert del_res.status_code == 200

    list_res = client.get('/api/tenants/current/members', headers=headers)
    assert list_res.get_json()['count'] == 1


def test_cannot_remove_last_owner(client, auth_headers, registered_user):
    """Cannot remove the sole owner of a workspace."""
    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    headers = {**auth_headers, 'X-Tenant-ID': str(membership.tenant_id)}

    res = client.delete(
        f'/api/tenants/current/members/{registered_user.id}',
        headers=headers
    )
    assert res.status_code == 400
    assert 'Cannot remove the last owner' in res.get_json()['error']


def test_normal_member_cannot_manage_members(client, app, db, registered_user, second_user):
    """A regular member cannot add or delete members."""
    from flask_jwt_extended import create_access_token

    membership = TenantMembership.query.filter_by(user_id=registered_user.id).first()
    # Add second_user as member
    db.session.add(TenantMembership(user_id=second_user.id, tenant_id=membership.tenant_id, role='member'))
    db.session.commit()

    with app.app_context():
        member_token = create_access_token(identity=second_user.email)
    member_headers = {
        'Authorization': f'Bearer {member_token}',
        'X-Tenant-ID': str(membership.tenant_id)
    }

    # Cannot add
    res = client.post(
        '/api/tenants/current/members',
        headers=member_headers,
        json={'email': 'other@example.com', 'role': 'member'}
    )
    assert res.status_code == 403

    # Cannot delete
    del_res = client.delete(
        f'/api/tenants/current/members/{registered_user.id}',
        headers=member_headers
    )
    assert del_res.status_code == 403


def test_admin_cannot_grant_owner_role(
    client, auth_headers, registered_user, second_user, db
):
    """An admin cannot grant the owner role to another user."""
    membership = TenantMembership.query.filter_by(
        user_id=registered_user.id
    ).first()

    membership.role = "admin"
    db.session.commit()

    headers = {
        **auth_headers,
        "X-Tenant-ID": str(membership.tenant_id)
    }

    response = client.post(
        "/api/tenants/current/members",
        headers=headers,
        json={
            "email": second_user.email,
            "role": "owner"
        }
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == (
        "Admins cannot assign the owner role"
    )

    added_membership = TenantMembership.query.filter_by(
        user_id=second_user.id,
        tenant_id=membership.tenant_id
    ).first()

    assert added_membership is None

