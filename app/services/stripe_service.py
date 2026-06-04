import stripe
from flask import current_app

def create_checkout_session(user_email):
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'AbyFlow Pro Plan'
                    },
                    'unit_amount': 2000
                },
                'quantity': 1
            }],
            mode='payment',
            success_url='http://localhost:5000/api/success',
            cancel_url='http://localhost:5000/api/cancel',
            customer_email=user_email,
            metadata={'user_email': user_email}
        )
        return session.url, None
    except Exception as e:
        return None, str(e)
    