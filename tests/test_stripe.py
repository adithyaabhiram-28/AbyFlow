import json
import time
from unittest.mock import patch, MagicMock
import stripe
from app.models import User, ProcessedEvent


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
    """Valid checkout.session.completed dispatches Celery task and records ProcessedEvent."""
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

    with patch('app.routes.webhooks.process_payment_task.delay') as mock_task:
        res = client.post('/api/webhooks/stripe', data=payload_bytes, headers={'Stripe-Signature': sig})
        assert res.status_code == 200
        assert res.get_json()['received'] is True
        mock_task.assert_called_once_with(user_email)

    # Verify event ID recorded in database
    with app.app_context():
        recorded = ProcessedEvent.query.filter_by(stripe_event_id=event_id).first()
        assert recorded is not None


def test_webhook_duplicate_event_id(client, app, db):
    """Duplicate webhook event is acknowledged without re-dispatching task."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_duplicate_test_456"
    user_email = "buyer@example.com"

    # Pre-record event in database
    with app.app_context():
        db.session.add(ProcessedEvent(stripe_event_id=event_id))
        db.session.commit()

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

    with patch('app.routes.webhooks.process_payment_task.delay') as mock_task:
        res = client.post('/api/webhooks/stripe', data=payload_bytes, headers={'Stripe-Signature': sig})
        assert res.status_code == 200
        data = res.get_json()
        assert data['received'] is True
        assert data.get('message') == 'Event already processed'
        assert not mock_task.called


def test_webhook_invoice_payment_failed(client, app, db, registered_user):
    """invoice.payment_failed downgrades user to free plan."""
    secret = app.config['STRIPE_WEBHOOK_SECRET']
    event_id = "evt_payment_failed_789"

    # Set user to pro first
    with app.app_context():
        user = User.query.filter_by(email=registered_user.email).first()
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

    res = client.post('/api/webhooks/stripe', data=payload_bytes, headers={'Stripe-Signature': sig})
    assert res.status_code == 200
    assert res.get_json()['received'] is True

    # Verify user downgraded
    with app.app_context():
        user = User.query.filter_by(email=registered_user.email).first()
        assert user.plan_tier == 'free'
