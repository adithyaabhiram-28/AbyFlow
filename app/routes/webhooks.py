import logging
import stripe
from flask import Blueprint, request, jsonify, current_app
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from app.schemas.stripe_schema import StripeWebhookSchema
from app.tasks import process_payment_task
from app.services.user_service import downgrade_user_to_free
from app.extensions import db
from app.models import ProcessedEvent

logger = logging.getLogger(__name__)
webhook_bp = Blueprint('webhooks', __name__)
webhook_schema = StripeWebhookSchema()

SUPPORTED_EVENTS = {
    'checkout.session.completed',
    'invoice.payment_failed',
}


def _extract_user_email(event_data: dict) -> str:
    """Extract user email from Stripe event object metadata or customer details."""
    obj = event_data.get('object', {})
    if not isinstance(obj, dict):
        if hasattr(obj, 'to_dict'):
            obj = obj.to_dict()
        else:
            obj = {}

    # Check metadata first
    metadata = obj.get('metadata') or {}
    if isinstance(metadata, dict) and metadata.get('user_email'):
        return metadata.get('user_email')

    # Check direct customer_email
    if obj.get('customer_email'):
        return obj.get('customer_email')

    # Check customer_details email
    customer_details = obj.get('customer_details') or {}
    if isinstance(customer_details, dict) and customer_details.get('email'):
        return customer_details.get('email')

    return ""


@webhook_bp.route('/stripe', methods=['POST'])
def handle_stripe_webhook():
    """Handle incoming Stripe webhook events securely with signature verification and idempotency."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")

    if not endpoint_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured.")
        return jsonify({"error": "Webhook configuration error"}), 500

    if not sig_header:
        logger.warning("Stripe webhook received without Stripe-Signature header.")
        return jsonify({"error": "Invalid signature"}), 400

    try:
        raw_event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            endpoint_secret
        )
    except ValueError:
        logger.warning("Stripe webhook: Invalid payload format.")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook: Signature verification failed.")
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        logger.error("Stripe webhook: Unexpected error during event construction: %s", str(e))
        return jsonify({"error": "Invalid webhook request"}), 400

    # Normalize to dictionary for safe access
    if hasattr(raw_event, 'to_dict'):
        event = raw_event.to_dict()
    elif isinstance(raw_event, dict):
        event = raw_event
    else:
        event = {}

    event_type = event.get('type')
    stripe_event_id = event.get('id')

    if not stripe_event_id:
        logger.warning("Stripe webhook event missing 'id' field.")
        return jsonify({"error": "Missing event ID"}), 400

    logger.info("Stripe webhook received: type=%s, id=%s", event_type, stripe_event_id)

    # Safely ignore unsupported events
    if event_type not in SUPPORTED_EVENTS:
        logger.info("Ignoring unsupported Stripe event type: %s", event_type)
        return jsonify({"received": True, "status": "ignored"}), 200

    # Check idempotency: event already processed
    existing_event = ProcessedEvent.query.filter_by(stripe_event_id=stripe_event_id).first()
    if existing_event:
        logger.info("Stripe event already processed: %s", stripe_event_id)
        return jsonify({"received": True, "message": "Event already processed"}), 200

    # Extract and validate user email
    event_data = event.get('data', {})
    user_email = _extract_user_email(event_data if isinstance(event_data, dict) else {})

    try:
        clean_data = webhook_schema.load({
            "type": event_type,
            "user_email": user_email
        })
    except ValidationError as err:
        logger.warning("Stripe webhook payload validation failed for event %s: %s", stripe_event_id, err.messages)
        return jsonify({"error": "Invalid event metadata"}), 400

    # Atomically record the processed event to prevent duplicate execution
    try:
        logged_event = ProcessedEvent(stripe_event_id=stripe_event_id)
        db.session.add(logged_event)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        logger.info("Stripe event %s caught by DB unique constraint race check.", stripe_event_id)
        return jsonify({"received": True, "message": "Event already processed"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error("Database error saving ProcessedEvent %s: %s", stripe_event_id, str(e))
        return jsonify({"error": "Internal database error"}), 500

    # Dispatch business logic
    validated_email = clean_data['user_email']
    if clean_data['event_type'] == 'checkout.session.completed':
        logger.info("Dispatching payment processing task for %s", validated_email)
        process_payment_task.delay(validated_email)
    elif clean_data['event_type'] == 'invoice.payment_failed':
        logger.info("Processing downgrade for payment failure: %s", validated_email)
        downgrade_user_to_free(validated_email)

    return jsonify({"received": True}), 200