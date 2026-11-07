
import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.services.tenant_service import (
    get_user_by_email,
    get_user_tenant_memberships,
    get_tenant_membership,
)

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
@jwt_required()
def get_current_tenant():
    """Resolve the requested tenant after validating membership."""
    current_email = get_jwt_identity()

    tenant_id_header = request.headers.get('X-Tenant-ID')

    if not tenant_id_header:
        return jsonify({
            'error': 'X-Tenant-ID header is required'
        }), 400

    try:
        tenant_id = int(tenant_id_header)

        if tenant_id <= 0:
            raise ValueError('Tenant ID must be positive')

    except (ValueError, TypeError):
        return jsonify({
            'error': 'X-Tenant-ID must be a positive integer'
        }), 400

    user = get_user_by_email(current_email)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    membership = get_tenant_membership(user, tenant_id)

    if not membership:
        logger.warning(
            "Tenant access denied: user=%s tenant_id=%s",
            current_email,
            tenant_id,
        )

        return jsonify({
            'error': 'You do not have access to this tenant'
        }), 403

    tenant = membership.tenant

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
