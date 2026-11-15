import os
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from flask import has_app_context, current_app
from celery import Celery
from sqlalchemy import or_

from app.extensions import db
from app.models import OutboxEvent, BillingEvent, User, Payment

logger = logging.getLogger(__name__)

redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

celery = Celery(
    'abyflow',
    broker=redis_url,
    backend=None
)

celery.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        'process-billing-outbox-every-30-seconds': {
            'task': 'app.tasks.process_outbox_task',
            'schedule': 30.0,
        },
    },
    beat_scheduler='celery.beat:PersistentScheduler',
    beat_schedule_filename='/tmp/abyflow-celerybeat-schedule',
)

_flask_app = None

MAX_OUTBOX_ATTEMPTS = 8
OUTBOX_BATCH_SIZE = 20
OUTBOX_LEASE_SECONDS = 120


def get_flask_app():
    global _flask_app

    if _flask_app is None:
        from app import create_app
        _flask_app = create_app()

    return _flask_app


@celery.task(bind=True, max_retries=3, default_retry_delay=5)
def process_payment_task(self, user_email: str):
    """Legacy task retained for backward compatibility."""
    from app.services import user_service

    app = get_flask_app()

    with app.app_context():
        try:
            success, message = user_service.upgrade_user_to_pro(user_email)

            if not success and "Database error" in message:
                raise self.retry(
                    exc=Exception(message),
                    countdown=5 * (2 ** self.request.retries)
                )

            return {
                "status": "success" if success else "failed",
                "message": message
            }

        except self.Retry:
            raise

        except Exception as exc:
            logger.exception("Legacy payment task failed.")
            raise self.retry(
                exc=exc,
                countdown=5 * (2 ** self.request.retries)
            )



def _claim_outbox_batch():
    """Claim one available event with a unique ownership token."""
    now = datetime.now(timezone.utc)

    # Move expired events that exhausted their retry budget to failed.
    exhausted_events = (
        OutboxEvent.query
        .filter(
            OutboxEvent.status == 'processing',
            OutboxEvent.locked_until <= now,
            OutboxEvent.attempts >= MAX_OUTBOX_ATTEMPTS
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    for event in exhausted_events:
        event.status = 'failed'
        event.locked_until = None
        event.lease_token = None
        event.last_error = (
            event.last_error
            or 'Outbox event exhausted its retry attempts.'
        )

        logger.error(
            "Outbox event %s moved to failed after exhausting retries.",
            event.id
        )

    # Find one pending event or one expired lease.
    eligible = or_(
        db.and_(
            OutboxEvent.status == 'pending',
            OutboxEvent.available_at <= now
        ),
        db.and_(
            OutboxEvent.status == 'processing',
            OutboxEvent.locked_until <= now
        )
    )

    event = (
        OutboxEvent.query
        .filter(
            eligible,
            OutboxEvent.attempts < MAX_OUTBOX_ATTEMPTS
        )
        .order_by(
            OutboxEvent.created_at.asc(),
            OutboxEvent.id.asc()
        )
        .with_for_update(skip_locked=True)
        .first()
    )

    if event is None:
        db.session.commit()
        return None

    lease_token = str(uuid4())

    event.status = 'processing'
    event.locked_until = (
        now + timedelta(seconds=OUTBOX_LEASE_SECONDS)
    )
    event.lease_token = lease_token
    event.attempts += 1

    claimed = {
        'id': event.id,
        'lease_token': lease_token
    }

    db.session.commit()

    return claimed


def _extract_checkout_email(payload):
    """Extract the account email from a persisted Stripe event."""
    event_data = payload.get('data') or {}
    checkout = event_data.get('object') or {}

    metadata = checkout.get('metadata') or {}
    customer_details = checkout.get('customer_details') or {}

    email = (
        metadata.get('user_email')
        or checkout.get('customer_email')
        or customer_details.get('email')
    )

    if not isinstance(email, str):
        return None

    email = email.strip().lower()

    return email or None

def _commit_outbox_fulfillment():
    """Commit payment fulfillment and outbox completion."""
    db.session.commit()

def _process_outbox_event(outbox_id, lease_token):
    """Apply payment fulfillment and complete the event atomically."""
    event = (
        OutboxEvent.query
        .filter_by(id=outbox_id)
        .with_for_update()
        .first()
    )

    if (
        event is None
        or event.status != 'processing'
        or event.lease_token != lease_token
    ):
        logger.info(
            "Skipping outbox event %s because this worker no longer owns its lease.",
            outbox_id
        )
        return False

    billing_event = BillingEvent.query.filter_by(
        id=event.billing_event_id
    ).first()

    if billing_event is None:
        raise ValueError("Associated billing event does not exist")

    if billing_event.event_type == 'checkout.session.completed':
        checkout = (
            (billing_event.payload.get('data') or {}).get('object') or {}
        )

        if checkout.get('payment_status') == 'paid':
            email = _extract_checkout_email(billing_event.payload)

            if not email:
                raise ValueError(
                    "Paid Checkout Session has no valid user email"
                )

            session_id = checkout.get('id')
            payment_intent = checkout.get('payment_intent')
            amount_cents = checkout.get('amount_total')
            currency = checkout.get('currency')

            if isinstance(payment_intent, dict):
                payment_intent = payment_intent.get('id')

            if not isinstance(session_id, str) or not session_id:
                raise ValueError("Missing Stripe Checkout Session ID")

            if (
                not isinstance(amount_cents, int)
                or isinstance(amount_cents, bool)
                or amount_cents != 2000
            ):
                raise ValueError(
                    "Stripe payment amount does not match the $20 Pro plan"
                )

            if currency != 'usd':
                raise ValueError(
                    "Stripe payment currency does not match USD"
                )

            user = User.query.filter_by(
                email=email
            ).with_for_update().first()

            if user is None:
                raise ValueError(
                    "Paid Checkout Session references an unknown user"
                )

            metadata = checkout.get('metadata') or {}
            user_id_meta = metadata.get('user_id')

            # Preserve compatibility with older Checkout Sessions.
            if user_id_meta is not None:
                user_id_text = str(user_id_meta)

                if (
                    isinstance(user_id_meta, bool)
                    or not user_id_text.isascii()
                    or not user_id_text.isdecimal()
                ):
                    raise ValueError(
                        "Paid Checkout Session has an invalid user ID"
                    )

                expected_user_id = int(user_id_text)

                if user.id != expected_user_id:
                    raise ValueError(
                        "Paid Checkout Session user ID does not match account"
                    )

            payment = Payment.query.filter_by(
                stripe_checkout_session_id=session_id
            ).with_for_update().first()

            if payment is None:
                payment = Payment(
                    user_id=user.id,
                    stripe_checkout_session_id=session_id,
                    stripe_payment_intent_id=payment_intent,
                    amount_cents=amount_cents,
                    currency='USD',
                    status='succeeded'
                )
                db.session.add(payment)

            else:
                if payment.user_id != user.id:
                    raise ValueError(
                        "Checkout Session is associated with another user"
                    )

                if (
                    payment.amount_cents != amount_cents
                    or payment.currency != 'USD'
                ):
                    raise ValueError(
                        "Existing payment details do not match Stripe"
                    )

                if (
                    payment.stripe_payment_intent_id
                    and payment_intent
                    and payment.stripe_payment_intent_id != payment_intent
                ):
                    raise ValueError(
                        "PaymentIntent ID conflicts with existing payment"
                    )

                if not payment.stripe_payment_intent_id:
                    payment.stripe_payment_intent_id = payment_intent

                payment.status = 'succeeded'

            user.plan_tier = 'pro'

            logger.info(
                "Payment %s persisted and Pro entitlement granted to user %s",
                session_id,
                user.id
            )

        else:
            logger.warning(
                "Checkout session %s is not paid; Pro access was not granted.",
                billing_event.stripe_event_id
            )

    elif billing_event.event_type == 'invoice.payment_failed':
        logger.info(
            "Recorded invoice.payment_failed event %s; no plan change applied.",
            billing_event.stripe_event_id
        )

    else:
        raise ValueError(
            f"Unsupported billing event type: {billing_event.event_type}"
        )

    event.status = 'completed'
    event.completed_at = datetime.now(timezone.utc)
    event.locked_until = None
    event.lease_token = None
    event.last_error = None

    # Payment, entitlement, and outbox completion commit together.
    _commit_outbox_fulfillment()
    return True



def _schedule_outbox_retry(outbox_id, lease_token, error):
    """Schedule exponential retry or move an exhausted event to failed."""
    event = OutboxEvent.query.filter_by(id=outbox_id).with_for_update().first()

    if (
        event is None
        or event.status != 'processing'
        or event.lease_token != lease_token
    ):
        logger.info(
            "Skipping retry for outbox event %s because this worker no longer owns its lease.",
            outbox_id
        )
        return False

    event.last_error = str(error)[:4000]
    event.locked_until = None
    event.lease_token = None

    if event.attempts >= MAX_OUTBOX_ATTEMPTS:
        event.status = 'failed'
        logger.error(
            "Outbox event %s exhausted retries: %s",
            outbox_id,
            error
        )
    else:
        delay_seconds = min(30 * (2 ** (event.attempts - 1)), 3600)

        event.status = 'pending'
        event.available_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=delay_seconds)
        )

        logger.warning(
            "Outbox event %s scheduled for retry in %s seconds.",
            outbox_id,
            delay_seconds
        )

    db.session.commit()
    return True


@celery.task(name='app.tasks.process_outbox_task')
def process_outbox_task():
    """Poll, claim, and process up to OUTBOX_BATCH_SIZE billing events."""
    app = (
        current_app._get_current_object()
        if has_app_context()
        else get_flask_app()
    )

    with app.app_context():
        processed = 0
        failed = 0
        claimed_count = 0

        for _ in range(OUTBOX_BATCH_SIZE):
            try:
                claim = _claim_outbox_batch()
            except Exception:
                db.session.rollback()
                logger.exception("Failed to claim a billing outbox event.")
                raise

            if claim is None:
                break

            claimed_count += 1
            outbox_id = claim['id']
            lease_token = claim['lease_token']

            try:
                did_process = _process_outbox_event(
                    outbox_id,
                    lease_token
                )
                if did_process:
                    processed += 1

            except Exception as exc:
                db.session.rollback()
                logger.exception(
                    "Outbox event %s processing failed.",
                    outbox_id
                )

                try:
                    _schedule_outbox_retry(
                        outbox_id,
                        lease_token,
                        exc
                    )
                except Exception:
                    db.session.rollback()
                    logger.exception(
                        "Could not schedule retry for outbox event %s.",
                        outbox_id
                    )

                failed += 1

        return {
            "claimed": claimed_count,
            "processed": processed,
            "failed": failed
        }
