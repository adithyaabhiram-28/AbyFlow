from flask import Blueprint, request, jsonify
from app.schemas.stripe_schema import StripeWebhookSchema
from app.tasks import process_payment_task
from app.extentions import db
from app.models import ProcessedEvent

webhook_bp = Blueprint('webhooks', __name__)
webhook_schema = StripeWebhookSchema()

@webhook_bp.route('/stripe', methods=['POST'])
def handle_stripe_webhook():
    payload = request.get_json()

    stripe_event_id = payload.get('id')
    if not stripe_event_id:
        return jsonify({"error":"Missing event ID"})
    
    if ProcessedEvent.query.filter_by(stripe_event_id=stripe_event_id).first():
        return jsonify({"message":"Event already processed"}), 200
    
    try:
        clean_data = webhook_schema.load({
            "type": payload.get("type"),
            "user_email": payload.get("data", {}).get("object", {}).get("metadata", {}).get("user_email")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    print("=" * 50)
    print("EVENT TYPE:", clean_data['event_type'])
    print("USER:", clean_data['user_email'])
    if clean_data['event_type'] == 'checkout.session.completed':
        process_payment_task.delay(clean_data['user_email'])
    elif clean_data['event_type'] == 'invoice.payment_failed':
        from app.services.user_service import downgrade_user_to_free
        downgrade_user_to_free(clean_data['user_email'])
    
    try:
        logged_event = ProcessedEvent(stripe_event_id=stripe_event_id)
        db.session.add(logged_event)
        db.session.commit()
    except Exception:
        pass
    
    return jsonify({"received": True}), 200