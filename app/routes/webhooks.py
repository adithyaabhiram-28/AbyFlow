from flask import Blueprint, request, jsonify
from app.schemas.stripe_schema import StripeWebhookSchema
from app.tasks import process_payment_task

webhook_bp = Blueprint('webhooks', __name__)
webhook_schema = StripeWebhookSchema()

@webhook_bp.route('/stripe', methods=['POST'])
def handle_stripe_webhook():
    payload = request.get_json()
    
    try:
        clean_data = webhook_schema.load({
            "type": payload.get("type"),
            "user_email": payload.get("data", {}).get("object", {}).get("metadata", {}).get("user_email")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
    if clean_data['event_type'] == 'checkout.session.completed':
        process_payment_task.delay(clean_data['user_email'])
    
    return jsonify({"received": True}), 200