
from functools import wraps

from flask import g, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.services.tenant_service import (
    get_user_by_email,
    get_tenant_membership,
    has_required_role,
)


def tenant_required(allowed_roles=None):
    """
    Require authentication and valid membership in the requested tenant.

    Args:
        allowed_roles: Optional set of permitted membership roles.
                       Example: {'owner', 'admin'}
    """
    roles = set(allowed_roles) if allowed_roles is not None else None

    def decorator(view_function):

        @wraps(view_function)
        @jwt_required()
        def wrapped_view(*args, **kwargs):

            # Resolve authenticated user.
            current_email = get_jwt_identity()
            user = get_user_by_email(current_email)

            if not user:
                return jsonify({'error': 'User not found'}), 404

            # Require explicit tenant context.
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

            # Verify membership in the requested tenant.
            membership = get_tenant_membership(user, tenant_id)

            if not membership:
                return jsonify({
                    'error': 'You do not have access to this tenant'
                }), 403

            # Apply role restrictions when requested.
            if roles is not None and not has_required_role(
                membership,
                roles,
            ):
                return jsonify({
                    'error': 'Insufficient permissions for this operation'
                }), 403

            # Expose validated context to the endpoint.
            g.current_user = user
            g.current_tenant = membership.tenant
            g.current_membership = membership

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator
