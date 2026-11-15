
import re
import logging

from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token,
    jwt_required,
    get_jwt_identity,
)

from app.extensions import db, bcrypt
from app.models import User, Tenant, TenantMembership

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email_format(email: str) -> bool:
    """Validate standard email pattern."""
    if not isinstance(email, str):
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


@auth_bp.route('/register', methods=['POST'])
def register():
    """Register a user and create their personal workspace atomically."""
    data = request.get_json(silent=True)

    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON request body'}), 400

    raw_email = data.get('email')
    raw_password = data.get('password')

    if (
        not raw_email
        or not raw_password
        or not isinstance(raw_email, str)
        or not isinstance(raw_password, str)
    ):
        return jsonify({'error': 'Email and password are required'}), 400

    email = raw_email.strip().lower()
    password = raw_password

    if not validate_email_format(email):
        return jsonify({'error': 'Invalid email format'}), 400

    if len(password) < 6:
        return jsonify({
            'error': 'Password must be at least 6 characters long'
        }), 400

    try:
        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            return jsonify({'error': 'Email already exists'}), 409

        hashed_password = bcrypt.generate_password_hash(
            password
        ).decode('utf-8')

        user = User(
            email=email,
            password_hash=hashed_password,
            plan_tier='free',
        )

        db.session.add(user)
        db.session.flush()

        tenant = Tenant(
            name='Personal Workspace',
            slug=f'personal-user-{user.id}',
        )

        db.session.add(tenant)
        db.session.flush()

        membership = TenantMembership(
            user_id=user.id,
            tenant_id=tenant.id,
            role='owner',
        )

        db.session.add(membership)
        db.session.commit()

        logger.info(
            "User registered successfully: %s (tenant_id=%s)",
            email,
            tenant.id,
        )

        return jsonify({
            'message': 'User created successfully',
            'email': user.email,
        }), 201

    except Exception:
        db.session.rollback()
        logger.exception("Error during user registration")
        return jsonify({'error': 'Internal server error'}), 500


@auth_bp.route('/login', methods=['POST'])
def login():
    """Authenticate a user and return a JWT access token."""
    data = request.get_json(silent=True)

    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Invalid email or password'}), 401

    raw_email = data.get('email')
    raw_password = data.get('password')

    if (
        not raw_email
        or not raw_password
        or not isinstance(raw_email, str)
        or not isinstance(raw_password, str)
    ):
        return jsonify({'error': 'Invalid email or password'}), 401

    email = raw_email.strip().lower()
    password = raw_password

    try:
        user = User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(
            user.password_hash,
            password,
        ):
            token = create_access_token(identity=user.email)

            logger.info("User logged in successfully: %s", email)

            return jsonify({
                'message': 'Login successful',
                'token': token,
            }), 200

        logger.warning("Failed login attempt for email: %s", email)
        return jsonify({'error': 'Invalid email or password'}), 401

    except Exception:
        logger.exception("Error during user login")
        return jsonify({'error': 'Internal server error'}), 500


@auth_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    """Return dashboard details for the authenticated user."""
    user_email = get_jwt_identity()

    if not user_email:
        return jsonify({'error': 'Invalid authentication token'}), 401

    try:
        user = User.query.filter_by(
            email=user_email.strip().lower()
        ).first()

        if not user:
            return jsonify({'error': 'User not found'}), 404

        return jsonify({
            'message': f"Welcome to AbyFlow, {user.email}",
            'email': user.email,
            'plan_tier': user.plan_tier,
        }), 200

    except Exception:
        logger.exception(
            "Error fetching dashboard for %s",
            user_email,
        )
        return jsonify({'error': 'Internal server error'}), 500
