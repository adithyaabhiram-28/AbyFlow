from flask import jsonify, Blueprint
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services import stripe_service

sub_bp = Blueprint('subscriptions', __name__)

@sub_bp.route('/create-checkout', methods=['POST'])
@jwt_required()
def create_checkout():
    current_user_email = get_jwt_identity()
    checkout_url, error = stripe_service.create_checkout_session(current_user_email)

    if error:
        return jsonify({'error' : 'Failed to create checkout session', 'details': error}), 500
    
    return jsonify({'checkout_url': checkout_url}), 200