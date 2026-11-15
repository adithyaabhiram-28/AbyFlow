from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.extensions import db
from app.models import BillingEvent, OutboxEvent
from app.tasks import _claim_outbox_batch

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import BillingEvent, OutboxEvent, Payment, User
from app.tasks import _claim_outbox_batch, _process_outbox_event


def test_concurrent_workers_claim_disjoint_outbox_events(postgres_app):
    """Concurrent workers claim every event once with distinct lease tokens."""
    event_count = 12
    start_barrier = Barrier(2)

    with postgres_app.app_context():
        billing_ids = []

        for index in range(event_count):
            event_key = f"evt_claim_race_{index}_{id(start_barrier)}"
            billing = BillingEvent(
                stripe_event_id=event_key,
                event_type="invoice.payment_failed",
                payload={
                    "id": event_key,
                    "type": "invoice.payment_failed",
                    "data": {"object": {}},
                },
            )
            db.session.add(billing)
            db.session.flush()

            outbox = OutboxEvent(
                billing_event_id=billing.id,
                status="pending",
                attempts=0,
            )
            db.session.add(outbox)
            billing_ids.append(billing.id)

        db.session.commit()

        expected_outbox_ids = {
            event.id
            for event in OutboxEvent.query.filter(
                OutboxEvent.billing_event_id.in_(billing_ids)
            ).all()
        }

    def worker():
        with postgres_app.app_context():
            start_barrier.wait(timeout=15)
            claims = []
            while True:
                claim = _claim_outbox_batch()
                if claim is None:
                    break
                claims.append(claim)
            return claims

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(worker)
            second_future = executor.submit(worker)
            first_claims = first_future.result(timeout=30)
            second_claims = second_future.result(timeout=30)

        first_ids = {claim["id"] for claim in first_claims}
        second_ids = {claim["id"] for claim in second_claims}
        all_claims = first_claims + second_claims

        assert first_ids.isdisjoint(second_ids)
        assert first_ids | second_ids == expected_outbox_ids
        assert len(all_claims) == event_count

        claimed_ids = [claim["id"] for claim in all_claims]
        lease_tokens = [claim["lease_token"] for claim in all_claims]

        assert len(set(claimed_ids)) == event_count
        assert len(set(lease_tokens)) == event_count
        assert all(lease_tokens)

        with postgres_app.app_context():
            claimed_events = OutboxEvent.query.filter(
                OutboxEvent.id.in_(expected_outbox_ids)
            ).all()

            assert len(claimed_events) == event_count
            assert all(event.status == "processing" for event in claimed_events)
            assert all(event.attempts == 1 for event in claimed_events)
            assert all(event.lease_token for event in claimed_events)

    finally:
        with postgres_app.app_context():
            db.session.rollback()

            for billing_id in billing_ids:
                outbox = OutboxEvent.query.filter_by(
                    billing_event_id=billing_id
                ).first()
                if outbox is not None:
                    db.session.delete(outbox)

                billing = db.session.get(BillingEvent, billing_id)
                if billing is not None:
                    db.session.delete(billing)

            db.session.commit()


def test_expired_lease_rejects_stale_worker_and_fulfills_once(
    postgres_app, postgres_workspace
):
    """A reclaimed lease fences the old worker and permits one fulfillment."""
    with postgres_app.app_context():
        user = db.session.get(User, postgres_workspace["user_id"])
        assert user is not None

        unique = uuid4().hex
        checkout = {
            "id": f"cs_test_{unique}",
            "object": "checkout.session",
            "payment_intent": f"pi_test_{unique}",
            "payment_status": "paid",
            "amount_total": 2000,
            "currency": "usd",
            "metadata": {"user_email": user.email},
        }

        billing = BillingEvent(
            stripe_event_id=f"evt_lease_race_{unique}",
            event_type="checkout.session.completed",
            payload={
                "id": f"evt_lease_race_{unique}",
                "type": "checkout.session.completed",
                "data": {"object": checkout},
            },
        )

        db.session.add(billing)
        db.session.flush()

        outbox = OutboxEvent(
            billing_event_id=billing.id,
            status="pending",
            attempts=0,
        )
        db.session.add(outbox)
        db.session.commit()

        billing_id = billing.id
        outbox_id = outbox.id

        try:
            # Worker A claims the event.
            claim_a = _claim_outbox_batch()

            assert claim_a is not None
            assert claim_a["id"] == outbox_id
            token_a = claim_a["lease_token"]

            # Simulate Worker A's lease expiring.
            outbox = db.session.get(OutboxEvent, outbox_id)
            outbox.locked_until = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            db.session.commit()

            # Worker B reclaims the expired event.
            claim_b = _claim_outbox_batch()

            assert claim_b is not None
            assert claim_b["id"] == outbox_id
            token_b = claim_b["lease_token"]

            assert token_b != token_a

            # Worker A must not fulfill using its stale token.
            assert _process_outbox_event(outbox_id, token_a) is False

            # Worker B owns the current lease and fulfills successfully.
            assert _process_outbox_event(outbox_id, token_b) is True

            db.session.refresh(outbox)

            payments = Payment.query.filter_by(
                stripe_checkout_session_id=checkout["id"]
            ).all()

            db.session.refresh(user)

            assert outbox.status == "completed"
            assert outbox.attempts == 2
            assert outbox.lease_token is None
            assert outbox.locked_until is None

            assert len(payments) == 1
            assert payments[0].status == "succeeded"
            assert payments[0].amount_cents == 2000

            assert user.plan_tier == "pro"

        finally:
            db.session.rollback()

            outbox = db.session.get(OutboxEvent, outbox_id)
            if outbox is not None:
                db.session.delete(outbox)

            billing = db.session.get(BillingEvent, billing_id)
            if billing is not None:
                db.session.delete(billing)

            db.session.commit()
