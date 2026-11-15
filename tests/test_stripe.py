from sqlalchemy.exc import IntegrityError
from uuid import uuid4
import json
import time
from unittest.mock import patch, MagicMock
import stripe
from app.models import (
    User,
    ProcessedEvent,
    BillingEvent,
    OutboxEvent,
)


def generate_mock_stripe_signature(payload_bytes: bytes, secret: str) -> str:
    """Generate a valid Stripe signature header using stripe's internal test tools."""
    timestamp = int(time.time())
    signature = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{payload_bytes.decode('utf-8')}",
        secret
    )
    return f"t={timestamp},v1={signature}"


def test_create_checkout_unauthorized(client):
    """Checkout endpoint requires authentication."""
    res = client.post('/api/subscriptions/create-checkout')
    assert res.status_code == 401


def test_create_checkout_success(client, auth_headers):
    """Checkout endpoint generates checkout URL when authenticated."""
    with patch('stripe.checkout.Session.create') as mock_create:
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/c/pay/cs_test_mock_session'
        mock_create.return_value = mock_session

        res = client.post('/api/subscriptions/create-checkout', headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert data['checkout_url'] == 'https://checkout.stripe.com/c/pay/cs_test_mock_session'
        assert mock_create.called
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs['mode'] == 'payment'
        assert call_kwargs['line_items'][0]['price_data']['unit_amount'] == 2000


def test_create_checkout_stripe_error(client, auth_headers):
    """Checkout endpoint returns 500 without leaking raw error on Stripe failure."""
    with patch('stripe.checkout.Session.create', side_effect=stripe.error.APIError("Stripe API down")):
        res = client.post('/api/subscriptions/create-checkout', headers=auth_headers)
        assert res.status_code == 500
        assert res.get_json()['error'] == 'Failed to create checkout session'


def test_checkout_success_and_cancel_routes(client):
    """Success and cancel routes return clean status responses."""
    success_res = client.get('/api/subscriptions/success?session_id=cs_123')
    assert success_res.status_code == 200
    assert success_res.get_json()['status'] == 'success'
    assert success_res.get_json()['session_id'] == 'cs_123'

    cancel_res = client.get('/api/subscriptions/cancel')
    assert cancel_res.status_code == 200
    assert cancel_res.get_json()['status'] == 'cancelled'


def test_webhook_missing_signature(client):
    """Webhook rejects request without Stripe-Signature."""
    res = client.post('/api/webhooks/stripe', json={'id': 'evt_123', 'object': 'event', 'type': 'checkout.session.completed'})
    assert res.status_code == 400
    assert 'Invalid signature' in res.get_json()['error']


def test_webhook_invalid_signature(client, app):
    """Webhook rejects request with invalid signature."""
    headers = {'Stripe-Signature': 't=123,v1=invalid_sig'}
    res = client.post('/api/webhooks/stripe', data=b'{"object": "event"}', headers=headers)
    assert res.status_code == 400
    assert 'Invalid signature' in res.get_json()['error']


def test_webhook_unsupported_event(client, app):
    """Webhook safely ignores unsupported event types."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    payload_dict = {
        "id": "evt_unsupported_1",
        "object": "event",
        "type": "customer.created",
        "data": {"object": {}}
    }
    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    sig = generate_mock_stripe_signature(payload_bytes, secret)

    res = client.post('/api/webhooks/stripe', data=payload_bytes, headers={'Stripe-Signature': sig})
    assert res.status_code == 200
    assert res.get_json().get('status') == 'ignored'


def test_webhook_missing_event_id(client, app):
    """Webhook returns 400 when event ID is missing."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    payload_dict = {
        "id": "",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_email": "test@example.com"}
            }
        }
    }
    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    sig = generate_mock_stripe_signature(payload_bytes, secret)

    res = client.post('/api/webhooks/stripe', data=payload_bytes, headers={'Stripe-Signature': sig})
    assert res.status_code == 400
    assert 'Missing event ID' in res.get_json()['error']


def test_webhook_missing_metadata_email(client, app):
    """Webhook returns 400 when user email metadata is missing."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    payload_dict = {
        "id": "evt_no_meta_1",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {}
            }
        }
    }
    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    sig = generate_mock_stripe_signature(payload_bytes, secret)

    res = client.post('/api/webhooks/stripe', data=payload_bytes, headers={'Stripe-Signature': sig})
    assert res.status_code == 400
    assert 'Invalid event metadata' in res.get_json()['error']


def test_webhook_checkout_session_completed_success(client, app, db):
    """Successful webhook atomically persists event and outbox records."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_valid_checkout_123"
    user_email = "buyer@example.com"

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_email": user_email}
            }
        }
    }

    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    sig = generate_mock_stripe_signature(payload_bytes, secret)

    res = client.post(
        '/api/webhooks/stripe',
        data=payload_bytes,
        headers={'Stripe-Signature': sig}
    )

    assert res.status_code == 200
    assert res.get_json()['received'] is True
    assert res.get_json()['status'] == 'queued'

    with app.app_context():
        processed = ProcessedEvent.query.filter_by(
            stripe_event_id=event_id
        ).first()

        billing = BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).first()

        assert processed is not None
        assert billing is not None
        assert billing.event_type == 'checkout.session.completed'
        assert billing.payload['id'] == event_id

        outbox = OutboxEvent.query.filter_by(
            billing_event_id=billing.id
        ).first()

        assert outbox is not None
        assert outbox.status == 'pending'
        assert outbox.attempts == 0


def test_webhook_duplicate_event_id(client, app, db):
    """Duplicate Stripe events are acknowledged without creating another outbox record."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_duplicate_test_456"

    with app.app_context():
        db.session.add(ProcessedEvent(stripe_event_id=event_id))
        db.session.commit()

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_email": "buyer@example.com"}
            }
        }
    }

    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    sig = generate_mock_stripe_signature(payload_bytes, secret)

    res = client.post(
        '/api/webhooks/stripe',
        data=payload_bytes,
        headers={'Stripe-Signature': sig}
    )

    assert res.status_code == 200
    assert res.get_json()['received'] is True
    assert res.get_json()['message'] == 'Event already received'

    with app.app_context():
        assert BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 0

        assert OutboxEvent.query.count() == 0


def test_webhook_invoice_payment_failed(client, app, db, registered_user):
    """Payment failure is persisted for asynchronous processing, not downgraded inline."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_payment_failed_789"

    with app.app_context():
        user = User.query.filter_by(
            email=registered_user.email
        ).first()

        user.plan_tier = 'pro'
        db.session.commit()

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "customer_email": registered_user.email
            }
        }
    }

    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    sig = generate_mock_stripe_signature(payload_bytes, secret)

    res = client.post(
        '/api/webhooks/stripe',
        data=payload_bytes,
        headers={'Stripe-Signature': sig}
    )

    assert res.status_code == 200
    assert res.get_json()['received'] is True
    assert res.get_json()['status'] == 'queued'

    with app.app_context():
        user = User.query.filter_by(
            email=registered_user.email
        ).first()

        assert user.plan_tier == 'pro'

        billing = BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).first()

        assert billing is not None
        assert billing.event_type == 'invoice.payment_failed'

        outbox = OutboxEvent.query.filter_by(
            billing_event_id=billing.id
        ).first()

        assert outbox is not None
        assert outbox.status == 'pending'


def test_webhook_repeated_delivery_is_idempotent(
    client, app, db, registered_user
):
    """Delivering the same Stripe event twice creates only one record set."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_repeated_delivery_123"

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {
                    "user_email": registered_user.email
                }
            }
        }
    }

    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    signature = generate_mock_stripe_signature(payload_bytes, secret)

    headers = {"Stripe-Signature": signature}

    first_response = client.post(
        "/api/webhooks/stripe",
        data=payload_bytes,
        headers=headers,
    )

    second_response = client.post(
        "/api/webhooks/stripe",
        data=payload_bytes,
        headers=headers,
    )

    assert first_response.status_code == 200
    assert first_response.get_json()["status"] == "queued"

    assert second_response.status_code == 200
    assert second_response.get_json()["message"] == "Event already received"

    with app.app_context():
        assert ProcessedEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 1

        assert BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 1

        billing = BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).one()

        assert OutboxEvent.query.filter_by(
            billing_event_id=billing.id
        ).count() == 1


def test_webhook_database_failure_rolls_back_all_records(
    client, app, db, registered_user, monkeypatch
):
    """A failed database commit must not leave partial webhook records."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_database_failure_123"

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {
                    "user_email": registered_user.email
                }
            }
        }
    }

    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    signature = generate_mock_stripe_signature(payload_bytes, secret)

    def fail_commit():
        raise RuntimeError("Simulated database commit failure")
    assert ProcessedEvent.query.filter_by(
        stripe_event_id=event_id
    ).first() is None
    monkeypatch.setattr(db.session, "commit", fail_commit)

    response = client.post(
        "/api/webhooks/stripe",
        data=payload_bytes,
        headers={"Stripe-Signature": signature},
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "Internal database error"

    with app.app_context():
        assert ProcessedEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 0

        assert BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 0

        assert OutboxEvent.query.count() == 0



def test_webhook_invoice_payment_failed_without_email(
    client, app, db
):
    """Payment failure events can be persisted without a user email."""
    secret = app.config["STRIPE_WEBHOOK_SECRET"]
    event_id = "evt_payment_failed_no_email_123"

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_test_invoice_123",
                "object": "invoice",
            }
        },
    }

    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    signature = generate_mock_stripe_signature(payload_bytes, secret)

    response = client.post(
        "/api/webhooks/stripe",
        data=payload_bytes,
        headers={"Stripe-Signature": signature},
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "queued"

    with app.app_context():
        billing = BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).one()

        assert billing.event_type == "invoice.payment_failed"

        outbox = OutboxEvent.query.filter_by(
            billing_event_id=billing.id
        ).one()

        assert outbox.status == "pending"


def test_webhook_unrelated_integrity_error_returns_500(
    client, app, db, registered_user, monkeypatch
):
    """Unrelated integrity errors must not be acknowledged as duplicates."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = f"evt_unrelated_integrity_error_{uuid4().hex}"

    payload_dict = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {
                    "user_email": registered_user.email
                }
            }
        }
    }

    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    signature = generate_mock_stripe_signature(payload_bytes, secret)

    def fail_commit():
        raise IntegrityError(
            "Simulated unrelated constraint violation",
            {},
            Exception("constraint violation")
        )
    db.session.remove()

    with app.app_context():
        assert ProcessedEvent.query.filter_by(
            stripe_event_id=event_id
        ).first() is None
    monkeypatch.setattr(db.session, "commit", fail_commit)

    response = client.post(
        "/api/webhooks/stripe",
        data=payload_bytes,
        headers={"Stripe-Signature": signature}
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "Internal database error"

    with app.app_context():
        assert ProcessedEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 0

        assert BillingEvent.query.filter_by(
            stripe_event_id=event_id
        ).count() == 0
