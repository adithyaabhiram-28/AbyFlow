import logging
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services import stripe_service

logger = logging.getLogger(__name__)
sub_bp = Blueprint('subscriptions', __name__)


@sub_bp.route('/create-checkout', methods=['POST'])
@jwt_required()
def create_checkout():
    """Initiate a Stripe checkout session for the authenticated user."""
    current_user_email = get_jwt_identity()
    if not current_user_email:
        return jsonify({'error': 'Unauthorized'}), 401

    from app.models import User
    user = User.query.filter_by(email=current_user_email.strip().lower()).first()
    user_id = user.id if user else None

    idempotency_key = request.headers.get('Idempotency-Key')
    checkout_url, error = stripe_service.create_checkout_session(
        user_email=current_user_email,
        user_id=user_id,
        idempotency_key=idempotency_key
    )

    if error:
        return jsonify({'error': 'Failed to create checkout session'}), 500

    return jsonify({'checkout_url': checkout_url}), 200



@sub_bp.route('/success', methods=['GET'])
def checkout_success():
    """Checkout success redirect destination."""
    session_id = request.args.get('session_id')
    return jsonify({
        'status': 'success',
        'message': 'Payment successful! Your account is being upgraded.',
        'session_id': session_id
    }), 200


@sub_bp.route('/cancel', methods=['GET'])
def checkout_cancel():
    """Checkout cancel redirect destination."""
    return jsonify({
        'status': 'cancelled',
        'message': 'Payment was cancelled. Your plan remains unchanged.'
    }), 200