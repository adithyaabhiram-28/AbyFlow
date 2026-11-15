
from functools import wraps

from flask import g, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.models import User


PLATFORM_ROLES = frozenset({
    'user',
    'operator',
    'admin',
})


def platform_role_required(allowed_roles):
    """
    Require authentication and one of the specified platform roles.

    Platform permissions are resolved from the database on every request.
    Tenant membership roles are deliberately not considered here.
    """
    if isinstance(allowed_roles, str):
        raise TypeError('allowed_roles must be an iterable of role names')

    roles = frozenset(allowed_roles)

    if not roles:
        raise ValueError('At least one platform role must be specified')

    invalid_roles = roles - PLATFORM_ROLES

    if invalid_roles:
        raise ValueError(
            f'Invalid platform roles: {sorted(invalid_roles)}'
        )

    def decorator(view_function):

        @wraps(view_function)
        @jwt_required()
        def wrapped_view(*args, **kwargs):

            identity = get_jwt_identity()

            if not isinstance(identity, str) or not identity.strip():
                return jsonify({
                    'error': 'Invalid authentication identity'
                }), 401

            user = (
                User.query
                .populate_existing()
                .filter_by(email=identity.strip().lower())
                .first()
            )

            if user is None:
                return jsonify({
                    'error': 'User not found'
                }), 404

            if user.platform_role not in roles:
                return jsonify({
                    'error': 'Insufficient platform permissions'
                }), 403

            # Expose the database-verified platform user.
            g.current_platform_user = user

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator
