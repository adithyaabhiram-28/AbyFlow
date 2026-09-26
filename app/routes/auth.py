import re
import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from app.extensions import db, bcrypt
from app.models import User

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
    """Register a new user with email and password."""
    data = request.get_json(silent=True)

    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON request body'}), 400

    raw_email = data.get('email')
    raw_password = data.get('password')

    if not raw_email or not raw_password or not isinstance(raw_email, str) or not isinstance(raw_password, str):
        return jsonify({'error': 'Email and password are required'}), 400

    email = raw_email.strip().lower()
    password = raw_password

    if not validate_email_format(email):
        return jsonify({'error': 'Invalid email format'}), 400

    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters long'}), 400

    try:
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return jsonify({'error': 'Email already exists'}), 409

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(email=email, password_hash=hashed_password, plan_tier='free')
        
        db.session.add(user)
        db.session.commit()

        logger.info("User registered successfully: %s", email)
        return jsonify({'message': 'User created successfully', 'email': user.email}), 201

    except Exception as e:
        db.session.rollback()
        logger.error("Error during user registration: %s", str(e))
        return jsonify({'error': 'Internal server error'}), 500


@auth_bp.route('/login', methods=['POST'])
def login():
    """Authenticate a user and return a JWT access token."""
    data = request.get_json(silent=True)

    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Invalid email or password'}), 401

    raw_email = data.get('email')
    raw_password = data.get('password')

    if not raw_email or not raw_password or not isinstance(raw_email, str) or not isinstance(raw_password, str):
        return jsonify({'error': 'Invalid email or password'}), 401

    email = raw_email.strip().lower()
    password = raw_password

    try:
        user = User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            # Issues token with configured expiration (JWT_ACCESS_TOKEN_EXPIRES)
            token = create_access_token(identity=user.email)
            logger.info("User logged in successfully: %s", email)
            return jsonify({'message': 'Login successful', 'token': token}), 200

        logger.warning("Failed login attempt for email: %s", email)
        return jsonify({'error': 'Invalid email or password'}), 401

    except Exception as e:
        logger.error("Error during user login: %s", str(e))
        return jsonify({'error': 'Internal server error'}), 500


@auth_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    """Return dashboard details for the authenticated user."""
    current_user_email = get_jwt_identity()
    if not current_user_email:
        return jsonify({'error': 'Invalid authentication token'}), 401

    try:
        user = User.query.filter_by(email=current_user_email.strip().lower()).first()
        if not user:
            return jsonify({'error': 'User not found'}), 404

        return jsonify({
            'message': f"Welcome to AbyFlow, {user.email}",
            'email': user.email,
            'plan_tier': user.plan_tier
        }), 200

    except Exception as e:
        logger.error("Error fetching dashboard for %s: %s", current_user_email, str(e))
        return jsonify({'error': 'Internal server error'}), 500