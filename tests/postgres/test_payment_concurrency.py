from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event, local
from time import monotonic, sleep
from uuid import uuid4

from sqlalchemy import text

import app.tasks as tasks
from app.extensions import db
from app.models import BillingEvent, OutboxEvent, Payment, User


def wait_until_blocked(app, backend_pid, timeout=8):
    deadline = monotonic() + timeout

    with app.app_context():
        with db.engine.connect() as connection:
            while monotonic() < deadline:
                blocked = connection.execute(
                    text(
                        "SELECT cardinality(pg_blocking_pids(:pid)) > 0"
                    ),
                    {"pid": backend_pid},
                ).scalar_one()

                if blocked:
                    return True

                sleep(0.02)

    return False


def test_concurrent_checkout_fulfillment_creates_one_payment(
    postgres_app, postgres_workspace, monkeypatch
):
    unique = uuid4().hex
    user_id = postgres_workspace["user_id"]
    email = f"concurrency-{unique}@example.com"
    session_id = f"cs_test_{unique}"
    payment_intent = f"pi_test_{unique}"

    with postgres_app.app_context():
        user = db.session.get(User, user_id)
        user.email = email

        checkout = {
            "id": session_id,
            "object": "checkout.session",
            "payment_intent": payment_intent,
            "payment_status": "paid",
            "amount_total": 2000,
            "currency": "usd",
            "metadata": {"user_email": email},
        }

        outbox_ids = []
        lease_tokens = []

        # Two distinct Stripe events reference the same Checkout Session.
        for index in range(2):
            billing = BillingEvent(
                stripe_event_id=f"evt_concurrency_{unique}_{index}",
                event_type="checkout.session.completed",
                payload={
                    "id": f"evt_concurrency_{unique}_{index}",
                    "type": "checkout.session.completed",
                    "data": {"object": checkout},
                },
            )
            db.session.add(billing)
            db.session.flush()

            lease_token = str(uuid4())
            outbox = OutboxEvent(
                billing_event_id=billing.id,
                status="processing",
                attempts=1,
                lease_token=lease_token,
            )
            db.session.add(outbox)
            db.session.flush()

            outbox_ids.append(outbox.id)
            lease_tokens.append(lease_token)

        db.session.commit()

    first_reached_commit = Event()
    release_first_transaction = Event()
    second_backend_pid = Queue()
    thread_state = local()
    original_commit = tasks._commit_outbox_fulfillment

    def controlled_commit():
        if getattr(thread_state, "hold_transaction", False):
            first_reached_commit.set()
            if not release_first_transaction.wait(timeout=15):
                raise TimeoutError(
                    "Timed out waiting to release first payment transaction"
                )
        return original_commit()

    monkeypatch.setattr(
        tasks,
        "_commit_outbox_fulfillment",
        controlled_commit,
    )

    def first_worker():
        with postgres_app.app_context():
            thread_state.hold_transaction = True
            return tasks._process_outbox_event(
                outbox_ids[0], lease_tokens[0]
            )

    def second_worker():
        with postgres_app.app_context():
            backend_pid = db.session.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            second_backend_pid.put(backend_pid)
            return tasks._process_outbox_event(
                outbox_ids[1], lease_tokens[1]
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_worker)

        assert first_reached_commit.wait(timeout=8), (
            "First payment transaction did not reach its commit point"
        )

        second_future = executor.submit(second_worker)

        try:
            backend_pid = second_backend_pid.get(timeout=8)
            assert wait_until_blocked(
                postgres_app,
                backend_pid,
            ), "Second payment transaction was not blocked by PostgreSQL"
        finally:
            release_first_transaction.set()

        assert first_future.result(timeout=15) is True
        assert second_future.result(timeout=15) is True

    with postgres_app.app_context():
        payments = Payment.query.filter_by(
            stripe_checkout_session_id=session_id
        ).all()
        user = db.session.get(User, user_id)
        outboxes = [
            db.session.get(OutboxEvent, outbox_id)
            for outbox_id in outbox_ids
        ]

        assert len(payments) == 1
        assert payments[0].stripe_payment_intent_id == payment_intent
        assert payments[0].amount_cents == 2000
        assert payments[0].status == "succeeded"
        assert user.plan_tier == "pro"
        assert all(event.status == "completed" for event in outboxes)
        assert all(event.lease_token is None for event in outboxes)

        # Remove test billing records before fixture teardown.
        billing_ids = [event.billing_event_id for event in outboxes]
        for outbox in outboxes:
            db.session.delete(outbox)

        for billing_id in billing_ids:
            billing = db.session.get(BillingEvent, billing_id)
            if billing is not None:
                db.session.delete(billing)

        db.session.delete(payments[0])
        db.session.commit()
