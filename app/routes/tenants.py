import logging

from flask import Blueprint, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.services.tenant_service import (
    get_user_by_email,
    get_user_tenant_memberships,
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