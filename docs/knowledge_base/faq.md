# AbyFlow Billing & Subscription FAQ

## What plans does AbyFlow offer?
AbyFlow offers a Free tier and a Pro tier. The Pro tier is available as a one-time upgrade for $20.00 USD (2000 cents).

## How are payments processed?
Payments are processed securely through Stripe Checkout. Card payments in USD are supported with server-side idempotency and transactional outbox fulfillment.

## How do upgrades take effect?
Upon successful payment confirmation via Stripe webhook, your account is immediately upgraded to Pro tier through our transactional outbox worker. Browser redirects alone never grant entitlements.

## What is the refund policy?
Refunds can be requested within 14 days of upgrade by contacting support or an authorized platform operator.

