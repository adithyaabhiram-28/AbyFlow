
import pytest

from flask import jsonify, g
from flask_jwt_extended import create_access_token

from app.models import User
from app.services.platform_authorization import (
    PLATFORM_ROLES,
    platform_role_required,
)


@pytest.fixture(autouse=True)
def platform_test_endpoint(app):
    """Register the test endpoint before any test sends a request."""

    @platform_role_required({'operator', 'admin'})
    def protected_endpoint():
        return jsonify({
            'message': 'Access granted',
            'email': g.current_platform_user.email,
        })

    app.add_url_rule(
        '/_test/platform-operations',
        endpoint='test_platform_operations',
        view_func=protected_endpoint,
    )


def test_platform_roles_are_defined():
    assert PLATFORM_ROLES == {
        'user',
        'operator',
        'admin',
    }


def test_invalid_platform_role_configuration_is_rejected():
    with pytest.raises(ValueError):
        platform_role_required(set())

    with pytest.raises(ValueError):
        platform_role_required({'superadmin'})

    with pytest.raises(TypeError):
        platform_role_required('admin')


def test_unauthenticated_user_is_rejected(client):
    response = client.get('/_test/platform-operations')

    assert response.status_code == 401


def test_normal_user_cannot_access_platform_operations(
    client, auth_headers, registered_user
):
    assert registered_user.platform_role == 'user'
    assert registered_user.tenant_memberships[0].role == 'owner'

    response = client.get(
        '/_test/platform-operations',
        headers=auth_headers,
    )

    assert response.status_code == 403
    assert response.get_json()['error'] == (
        'Insufficient platform permissions'
    )


@pytest.mark.parametrize('role', ['operator', 'admin'])
def test_authorized_platform_roles_can_access_operations(
    client, db, auth_headers, registered_user, role
):
    registered_user.platform_role = role
    db.session.commit()

    response = client.get(
        '/_test/platform-operations',
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.get_json()['message'] == 'Access granted'
    assert response.get_json()['email'] == registered_user.email


def test_revoked_platform_privilege_takes_effect_immediately(
    client, db, auth_headers, registered_user
):
    registered_user.platform_role = 'operator'
    db.session.commit()

    first_response = client.get(
        '/_test/platform-operations',
        headers=auth_headers,
    )

    assert first_response.status_code == 200

    # Revoke the role without issuing a new JWT.
    registered_user.platform_role = 'user'
    db.session.commit()

    second_response = client.get(
        '/_test/platform-operations',
        headers=auth_headers,
    )

    assert second_response.status_code == 403


def test_missing_database_user_is_rejected(client, app):
    with app.app_context():
        token = create_access_token(
            identity='deleted-user@example.com'
        )

    response = client.get(
        '/_test/platform-operations',
        headers={'Authorization': f'Bearer {token}'},
    )

    assert response.status_code == 404
    assert response.get_json()['error'] == 'User not found'
