import logging
import stripe
from flask import current_app

logger = logging.getLogger(__name__)


def create_checkout_session(user_email: str, user_id: int = None, idempotency_key: str = None):
    """
    Create a Stripe Checkout Session for one-time payment.
    Preserves mode='payment' with $20 USD one-time charge (2000 cents).
    Includes internal user_id in metadata alongside user_email for backward compatibility.
    Applies Stripe idempotency protection when key is provided.
    Avoids logging raw personal email addresses.
    """
    stripe_key = current_app.config.get('STRIPE_SECRET_KEY')
    if not stripe_key:
        logger.error("STRIPE_SECRET_KEY is not configured.")
        return None, "Stripe configuration error"

    stripe.api_key = stripe_key
    app_base_url = current_app.config.get('APP_BASE_URL', 'http://localhost:5000').rstrip('/')

    metadata = {'user_email': user_email}
    if user_id is not None:
        metadata['user_id'] = str(user_id)

    create_kwargs = {
        'payment_method_types': ['card'],
        'line_items': [{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': 'AbyFlow Pro Plan'
                },
                'unit_amount': 2000  # $20.00 USD
            },
            'quantity': 1
        }],
        'mode': 'payment',
        'success_url': f"{app_base_url}/api/subscriptions/success?session_id={{CHECKOUT_SESSION_ID}}",
        'cancel_url': f"{app_base_url}/api/subscriptions/cancel",
        'customer_email': user_email,
        'metadata': metadata,
    }

    if idempotency_key:
        create_kwargs['idempotency_key'] = idempotency_key

    try:
        session = stripe.checkout.Session.create(**create_kwargs)
        logger.info("Stripe Checkout Session created (user_id=%s)", user_id or "unspecified")
        return session.url, None

    except stripe.error.StripeError as e:
        logger.error("Stripe API error creating checkout session (user_id=%s): %s", user_id or "unspecified", str(e))
        return None, "Payment provider error"
    except Exception as e:
        logger.error("Unexpected error creating checkout session (user_id=%s): %s", user_id or "unspecified", str(e))
        return None, "Internal server error"
