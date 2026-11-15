
import logging

import stripe
from flask import Blueprint, request, jsonify, current_app
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from app.schemas.stripe_schema import StripeWebhookSchema
from app.extensions import db
from app.models import ProcessedEvent, BillingEvent, OutboxEvent

logger = logging.getLogger(__name__)

webhook_bp = Blueprint('webhooks', __name__)
webhook_schema = StripeWebhookSchema()

SUPPORTED_EVENTS = {
    'checkout.session.completed',
    'invoice.payment_failed',
}


@webhook_bp.route('/stripe', methods=['POST'])
def handle_stripe_webhook():
    """Verify and atomically persist supported Stripe events."""

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET')

    if not endpoint_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured.")
        return jsonify({"error": "Webhook configuration error"}), 500

    if not sig_header:
        logger.warning("Stripe webhook received without Stripe-Signature.")
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
    except Exception:
        logger.exception("Unexpected Stripe webhook verification error.")
        return jsonify({"error": "Invalid webhook request"}), 400

    if hasattr(raw_event, 'to_dict'):
        event = raw_event.to_dict()
    elif isinstance(raw_event, dict):
        event = raw_event
    else:
        return jsonify({"error": "Invalid event format"}), 400

    event_type = event.get('type')
    stripe_event_id = event.get('id')

    if not stripe_event_id:
        return jsonify({"error": "Missing event ID"}), 400

    if event_type not in SUPPORTED_EVENTS:
        logger.info("Ignoring unsupported Stripe event: %s", event_type)
        return jsonify({"received": True, "status": "ignored"}), 200

    event_data = event.get('data', {})
    event_object = (
        event_data.get('object', {})
        if isinstance(event_data, dict)
        else {}
    )

    if hasattr(event_object, 'to_dict'):
        event_object = event_object.to_dict()

    if not isinstance(event_object, dict):
        event_object = {}

    metadata = event_object.get('metadata') or {}
    customer_details = event_object.get('customer_details') or {}

    user_email = (
        metadata.get('user_email')
        if isinstance(metadata, dict) else None
    ) or event_object.get('customer_email') or (
        customer_details.get('email')
        if isinstance(customer_details, dict) else None
    ) or ""

    try:
        schema_payload = {"type": event_type}

        if event_type == "checkout.session.completed":
            schema_payload["user_email"] = user_email

        clean_data = webhook_schema.load(schema_payload)

        if (
            event_type == "checkout.session.completed"
            and not clean_data.get("user_email")
        ):
            return jsonify({"error": "Invalid event metadata"}), 400

    except ValidationError as err:
        logger.warning(
            "Invalid event metadata for Stripe event %s: %s",
            stripe_event_id,
            err.messages
        )
        return jsonify({"error": "Invalid event metadata"}), 400

    # Preserve historical idempotency behavior.
    # Events already recorded by the previous webhook implementation
    # must not be processed again.
    existing_event = ProcessedEvent.query.filter_by(
        stripe_event_id=stripe_event_id
    ).first()

    if existing_event:
        logger.info(
            "Stripe event %s was already received.",
            stripe_event_id
        )
        return jsonify({
            "received": True,
            "message": "Event already received"
        }), 200

    try:
        # Persist all three records in one atomic transaction.
        processed_event = ProcessedEvent(
            stripe_event_id=stripe_event_id
        )

        billing_event = BillingEvent(
            stripe_event_id=stripe_event_id,
            event_type=event_type,
            payload=event
        )

        db.session.add(processed_event)
        db.session.add(billing_event)
        db.session.flush()

        outbox_event = OutboxEvent(
            billing_event_id=billing_event.id,
            status='pending'
        )

        db.session.add(outbox_event)
        db.session.commit()

    except IntegrityError:
        db.session.rollback()

        # logger.warning(
        #     "WEBHOOK DIAGNOSTIC: event_id=%s existing=%s",
        #     stripe_event_id,
        #     ProcessedEvent.query.filter_by(
        #         stripe_event_id=stripe_event_id
        #     ).count()
        # )

        # A concurrent request may have persisted this event first.
        # Only acknowledge it as a duplicate if the marker exists.
        existing_event = ProcessedEvent.query.filter_by(
            stripe_event_id=stripe_event_id
        ).first()

        if existing_event:
            logger.info(
                "Duplicate Stripe event delivery: %s",
                stripe_event_id
            )
            return jsonify({
                "received": True,
                "message": "Event already received"
            }), 200

        logger.exception(
            "Unexpected integrity error persisting Stripe event %s",
            stripe_event_id
        )
        return jsonify({"error": "Internal database error"}), 500

    except Exception:
        db.session.rollback()
        logger.exception(
            "Failed to persist Stripe event %s",
            stripe_event_id
        )
        return jsonify({"error": "Internal database error"}), 500

    logger.info(
        "Stripe event persisted to outbox: id=%s type=%s",
        stripe_event_id,
        event_type
    )

    return jsonify({
        "received": True,
        "status": "queued"
    }), 200
