import logging

from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.services.tenant_service import (
    get_user_by_email,
    get_user_tenant_memberships,
    create_tenant,
    list_tenant_members,
    add_tenant_member,
    update_tenant_member_role,
    remove_tenant_member,
    TenantValidationError,
    TenantNotFoundError,
    TenantMemberNotFoundError,
    TenantConflictError,
)
from app.services.tenant_authorization import tenant_required

logger = logging.getLogger(__name__)

tenant_bp = Blueprint('tenants', __name__)


@tenant_bp.route('', methods=['GET'])
@jwt_required()
def list_tenants():
    """List all workspaces accessible to the authenticated user."""
    current_email = get_jwt_identity()

    user = get_user_by_email(current_email)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    memberships = get_user_tenant_memberships(user)

    tenants = [
        {
            'id': membership.tenant.id,
            'name': membership.tenant.name,
            'slug': membership.tenant.slug,
            'role': membership.role,
        }
        for membership in memberships
    ]

    return jsonify({
        'tenants': tenants,
        'count': len(tenants),
    }), 200


@tenant_bp.route('', methods=['POST'])
@jwt_required()
def create_tenant_route():
    """Create a new workspace and assign the authenticated user as owner."""
    current_email = get_jwt_identity()
    user = get_user_by_email(current_email)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON request body'}), 400

    name = data.get('name')
    slug = data.get('slug')

    try:
        tenant = create_tenant(user=user, name=name, slug=slug)
        return jsonify({
            'message': 'Workspace created successfully',
            'tenant': {
                'id': tenant.id,
                'name': tenant.name,
                'slug': tenant.slug,
                'role': 'owner',
            }
        }), 201
    except TenantValidationError as e:
        return jsonify({'error': str(e)}), 400
    except TenantConflictError as e:
        return jsonify({'error': str(e)}), 409


@tenant_bp.route('/current', methods=['GET'])
@tenant_required()
def get_current_tenant():
    """Return the authenticated user's validated tenant context."""
    tenant = g.current_tenant
    membership = g.current_membership

    return jsonify({
        'tenant': {
            'id': tenant.id,
            'name': tenant.name,
            'slug': tenant.slug,
        },
        'membership': {
            'role': membership.role,
        },
    }), 200


@tenant_bp.route('/current/members', methods=['GET'])
@tenant_required()
def list_current_members():
    """List members belonging to the current tenant workspace."""
    members = list_tenant_members(g.current_tenant.id)
    return jsonify({
        'members': members,
        'count': len(members),
    }), 200


@tenant_bp.route('/current/members', methods=['POST'])
@tenant_required(allowed_roles={'owner', 'admin'})
def add_current_member():
    """Add a new member to the current tenant workspace."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON request body'}), 400

    email = data.get('email')
    role = data.get('role', 'member')

    if g.current_membership.role == 'admin' and role == 'owner':
        return jsonify({
            'error': 'Admins cannot assign the owner role'
        }), 403

    try:
        member = add_tenant_member(
            tenant_id=g.current_tenant.id,
            email=email,
            role=role
        )
        return jsonify({
            'message': 'Member added successfully',
            'member': member,
        }), 201
    except TenantValidationError as e:
        return jsonify({'error': str(e)}), 400
    except TenantConflictError as e:
        return jsonify({'error': str(e)}), 409


@tenant_bp.route('/current/members/<int:member_user_id>', methods=['PATCH'])
@tenant_required(allowed_roles={'owner'})
def update_current_member_role(member_user_id):
    """Update role for a member in the current tenant workspace."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON request body'}), 400

    new_role = data.get('role')
    try:
        member = update_tenant_member_role(
            tenant_id=g.current_tenant.id,
            target_user_id=member_user_id,
            new_role=new_role
        )
        return jsonify({
            'message': 'Member role updated successfully',
            'member': member,
        }), 200
    except TenantValidationError as e:
        return jsonify({'error': str(e)}), 400
    except TenantMemberNotFoundError as e:
        return jsonify({'error': str(e)}), 404


@tenant_bp.route('/current/members/<int:member_user_id>', methods=['DELETE'])
@tenant_required(allowed_roles={'owner'})
def remove_current_member(member_user_id):
    """Remove a member from the current tenant workspace."""
    try:
        remove_tenant_member(
            tenant_id=g.current_tenant.id,
            target_user_id=member_user_id
        )
        return jsonify({
            'message': 'Member removed successfully',
        }), 200
    except TenantValidationError as e:
        return jsonify({'error': str(e)}), 400
    except TenantMemberNotFoundError as e:
        return jsonify({'error': str(e)}), 404
