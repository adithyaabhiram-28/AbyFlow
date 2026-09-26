import logging
import stripe
from flask import current_app

logger = logging.getLogger(__name__)


def create_checkout_session(user_email: str):
    """
    Create a Stripe Checkout Session for one-time payment.
    Preserves mode='payment' with $20 USD one-time charge.
    """
    stripe_key = current_app.config.get('STRIPE_SECRET_KEY')
    if not stripe_key:
        logger.error("STRIPE_SECRET_KEY is not configured.")
        return None, "Stripe configuration error"

    stripe.api_key = stripe_key
    app_base_url = current_app.config.get('APP_BASE_URL', 'http://localhost:5000').rstrip('/')

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'AbyFlow Pro Plan'
                    },
                    'unit_amount': 2000  # $20.00 USD
                },
                'quantity': 1
            }],
            mode='payment',
            success_url=f"{app_base_url}/api/subscriptions/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_base_url}/api/subscriptions/cancel",
            customer_email=user_email,
            metadata={'user_email': user_email}
        )
        logger.info("Stripe Checkout Session created for user: %s", user_email)
        return session.url, None

    except stripe.error.StripeError as e:
        logger.error("Stripe API error creating checkout session for %s: %s", user_email, str(e))
        return None, "Payment provider error"
    except Exception as e:
        logger.error("Unexpected error creating checkout session for %s: %s", user_email, str(e))
        return None, "Internal server error"