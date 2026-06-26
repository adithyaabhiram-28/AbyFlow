import stripe
import os
from flask import Blueprint, request, jsonify, current_app
from app.schemas.stripe_schema import StripeWebhookSchema
from app.tasks import process_payment_task
from app.extentions import db
from app.models import ProcessedEvent

webhook_bp = Blueprint('webhooks', __name__)
webhook_schema = StripeWebhookSchema()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

@webhook_bp.route('/stripe', methods=['POST'])
def handle_stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = current_app.config["STRIPE_WEBHOOK_SECRET"]

    print("Webhook secret:", endpoint_secret)

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            endpoint_secret
        )
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400

    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    payload = event.to_dict()

    event_type = payload["type"]

    print("Received:", event_type)

    if event_type not in (
        "checkout.session.completed",
        "invoice.payment_failed",
    ):
        return jsonify({"received": True}), 200

    stripe_event_id = payload.get('id')
    if not stripe_event_id:
        return jsonify({"error":"Missing event ID"})
    
    if ProcessedEvent.query.filter_by(stripe_event_id=stripe_event_id).first():
        return jsonify({"message":"Event already processed"}), 200
    
    try:
        print(payload)
        clean_data = webhook_schema.load({
            "type": payload.get("type"),
            "user_email": payload.get("data", {}).get("object", {}).get("metadata", {}).get("user_email")
        })
    except Exception as e:
        print("Schema Error:", e)
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