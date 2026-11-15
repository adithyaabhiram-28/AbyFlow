from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models import BillingEvent, OutboxEvent, Payment, User
from app.tasks import process_outbox_task, _process_outbox_event


def checkout_payload(email, payment_status="paid", amount_cents=2000):
    return {
        "id": f"cs_test_{uuid4().hex}",
        "object": "checkout.session",
        "payment_intent": f"pi_test_{uuid4().hex}",
        "payment_status": payment_status,
        "amount_total": amount_cents,
        "currency": "usd",
        "metadata": {"user_email": email},
    }


def create_outbox_event(db, event_type, checkout_object):
    payload = {
        "id": f"evt_test_{uuid4().hex}",
        "object": "event",
        "type": event_type,
        "data": {"object": checkout_object},
    }

    billing = BillingEvent(
        stripe_event_id=payload["id"],
        event_type=event_type,
        payload=payload,
    )
    db.session.add(billing)
    db.session.flush()

    outbox = OutboxEvent(
        billing_event_id=billing.id,
        status="pending",
    )
    db.session.add(outbox)
    db.session.commit()
    return outbox.id, billing.id


def mark_event_processing(db, outbox_id):
    """Simulate a worker claim and return its ownership token."""
    outbox = db.session.get(OutboxEvent, outbox_id)
    token = str(uuid4())
    outbox.status = "processing"
    outbox.locked_until = datetime.now(timezone.utc) + timedelta(minutes=2)
    outbox.lease_token = token
    outbox.attempts += 1
    db.session.commit()
    return token


def test_paid_checkout_creates_payment_and_upgrades_user(
    app, db, registered_user
):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        assert _process_outbox_event(outbox_id, lease_token) is True

        user = User.query.filter_by(email=registered_user.email).one()
        payment = Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).one()
        outbox = db.session.get(OutboxEvent, outbox_id)

        assert user.plan_tier == "pro"
        assert payment.user_id == user.id
        assert payment.stripe_payment_intent_id == checkout["payment_intent"]
        assert payment.amount_cents == 2000
        assert payment.currency == "USD"
        assert payment.status == "succeeded"
        assert outbox.status == "completed"
        assert outbox.completed_at is not None
        assert outbox.locked_until is None
        assert outbox.lease_token is None


def test_unpaid_checkout_does_not_create_payment_or_upgrade_user(
    app, db, registered_user
):
    with app.app_context():
        checkout = checkout_payload(
            registered_user.email, payment_status="unpaid"
        )
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        assert _process_outbox_event(outbox_id, lease_token) is True

        user = User.query.filter_by(email=registered_user.email).one()
        outbox = db.session.get(OutboxEvent, outbox_id)

        assert user.plan_tier != "pro"
        assert Payment.query.count() == 0
        assert outbox.status == "completed"


def test_payment_failed_event_does_not_downgrade_user(
    app, db, registered_user
):
    with app.app_context():
        registered_user.plan_tier = "pro"
        db.session.commit()

        outbox_id, _ = create_outbox_event(
            db,
            "invoice.payment_failed",
            {"metadata": {"user_email": registered_user.email}},
        )
        lease_token = mark_event_processing(db, outbox_id)

        assert _process_outbox_event(outbox_id, lease_token) is True

        user = User.query.filter_by(email=registered_user.email).one()
        outbox = db.session.get(OutboxEvent, outbox_id)

        assert user.plan_tier == "pro"
        assert outbox.status == "completed"


def test_unknown_user_raises_processing_error(app, db):
    with app.app_context():
        checkout = checkout_payload("unknown-user@example.com")
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        with pytest.raises(ValueError, match="unknown user"):
            _process_outbox_event(outbox_id, lease_token)

        db.session.rollback()
        outbox = db.session.get(OutboxEvent, outbox_id)
        assert outbox.status == "processing"
        assert outbox.lease_token == lease_token
        assert Payment.query.count() == 0


def test_completed_event_is_not_processed_again(app, db, registered_user):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        assert _process_outbox_event(outbox_id, lease_token) is True
        assert _process_outbox_event(outbox_id, lease_token) is False

        assert Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).count() == 1
        assert db.session.get(OutboxEvent, outbox_id).status == "completed"


def test_stale_worker_cannot_process_reclaimed_event(app, db, registered_user):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        stale_token = mark_event_processing(db, outbox_id)
        outbox = db.session.get(OutboxEvent, outbox_id)
        current_token = str(uuid4())
        outbox.lease_token = current_token
        db.session.commit()

        assert _process_outbox_event(outbox_id, stale_token) is False
        assert Payment.query.count() == 0
        assert User.query.filter_by(
            email=registered_user.email
        ).one().plan_tier != "pro"
        assert db.session.get(OutboxEvent, outbox_id).lease_token == current_token


def test_paid_checkout_rejects_incorrect_amount(app, db, registered_user):
    with app.app_context():
        checkout = checkout_payload(
            registered_user.email, amount_cents=2500
        )
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        with pytest.raises(ValueError, match="amount"):
            _process_outbox_event(outbox_id, lease_token)

        db.session.rollback()
        assert Payment.query.count() == 0
        assert User.query.filter_by(
            email=registered_user.email
        ).one().plan_tier != "pro"


def test_outbox_task_processes_pending_events(app, db, registered_user):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        result = process_outbox_task()

        assert result["claimed"] == 1
        assert result["processed"] == 1
        assert result["failed"] == 0
        assert db.session.get(OutboxEvent, outbox_id).status == "completed"
        assert Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).count() == 1


def test_expired_lease_can_be_reclaimed(app, db, registered_user):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        old_token = mark_event_processing(db, outbox_id)

        outbox = db.session.get(OutboxEvent, outbox_id)
        outbox.locked_until = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        db.session.commit()

        result = process_outbox_task()

        db.session.refresh(outbox)

        assert result["claimed"] == 1
        assert result["processed"] == 1
        assert result["failed"] == 0
        assert outbox.status == "completed"
        assert outbox.attempts == 2
        assert outbox.lease_token is None
        assert old_token != outbox.lease_token
        assert Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).count() == 1


def test_exhausted_expired_event_moves_to_failed(app, db, registered_user):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        outbox = db.session.get(OutboxEvent, outbox_id)
        outbox.status = "processing"
        outbox.attempts = 8
        outbox.locked_until = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        outbox.lease_token = str(uuid4())
        outbox.last_error = "Previous processing failure"
        db.session.commit()

        result = process_outbox_task()

        outbox = db.session.get(OutboxEvent, outbox_id)

        assert result["claimed"] == 0
        assert outbox.status == "failed"
        assert outbox.locked_until is None
        assert outbox.lease_token is None
        assert outbox.last_error == "Previous processing failure"


def test_worker_crash_during_fulfillment_recovers_without_duplicate_payment(
    app, db, registered_user, monkeypatch
):
    """A crash before commit must leave the event recoverable."""
    import app.tasks as tasks

    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        # Worker 1 claims the event and commits its processing lease.
        claim = tasks._claim_outbox_batch()
        assert claim is not None
        assert claim["id"] == outbox_id
        lease_token = claim["lease_token"]

        outbox = db.session.get(OutboxEvent, outbox_id)
        assert outbox.status == "processing"
        assert outbox.attempts == 1
        assert outbox.lease_token == lease_token

        def simulate_worker_crash():
            raise SystemExit("Simulated worker termination")

        monkeypatch.setattr(
            tasks,
            "_commit_outbox_fulfillment",
            simulate_worker_crash,
        )

        with pytest.raises(SystemExit, match="Simulated worker termination"):
            tasks._process_outbox_event(outbox_id, lease_token)

        db.session.rollback()

        assert Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).count() == 0

        outbox = db.session.get(OutboxEvent, outbox_id)
        assert outbox.status == "processing"
        assert outbox.lease_token == lease_token

        # Simulate the crashed worker's lease expiring.
        outbox.locked_until = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        db.session.commit()
        monkeypatch.undo()

        result = process_outbox_task()

        assert result["claimed"] == 1
        assert result["processed"] == 1
        assert result["failed"] == 0

        payments = Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).all()
        outbox = db.session.get(OutboxEvent, outbox_id)
        user = User.query.filter_by(
            email=registered_user.email
        ).one()

        assert len(payments) == 1
        assert payments[0].amount_cents == 2000
        assert payments[0].status == "succeeded"
        assert user.plan_tier == "pro"
        assert outbox.status == "completed"
        assert outbox.attempts == 2
        assert outbox.locked_until is None
        assert outbox.lease_token is None


def test_retry_backoff_and_one_hour_cap(app, db, registered_user):
    from app.tasks import _schedule_outbox_retry

    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        outbox = db.session.get(OutboxEvent, outbox_id)
        token = mark_event_processing(db, outbox_id)

        expected_delays = [30, 60, 120, 240, 480, 960, 1920, 3600]

        for attempt, expected_delay in enumerate(expected_delays, start=1):
            outbox = db.session.get(OutboxEvent, outbox_id)
            outbox.attempts = attempt
            outbox.status = "processing"
            outbox.lease_token = token
            outbox.locked_until = (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            )
            db.session.commit()

            before = datetime.now(timezone.utc)

            result = _schedule_outbox_retry(
                outbox_id, token, "Simulated processing failure"
            )

            after = datetime.now(timezone.utc)
            db.session.refresh(outbox)

            assert result is True
            assert outbox.status == (
                "failed" if attempt == 8 else "pending"
            )
            assert outbox.last_error == "Simulated processing failure"
            assert outbox.locked_until is None
            assert outbox.lease_token is None

            if attempt < 8:
                available_at = outbox.available_at

                if available_at.tzinfo is None:
                    available_at = available_at.replace(tzinfo=timezone.utc)

                actual_delay = (available_at - before).total_seconds()
                assert expected_delay <= actual_delay <= (
                    expected_delay
                    + (after - before).total_seconds()
                    + 1
                )


def test_retry_scheduling_rejects_stale_lease_token(
    app, db, registered_user
):
    from app.tasks import _schedule_outbox_retry

    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        old_token = mark_event_processing(db, outbox_id)
        outbox = db.session.get(OutboxEvent, outbox_id)

        new_token = str(uuid4())
        outbox.lease_token = new_token
        db.session.commit()

        result = _schedule_outbox_retry(
            outbox_id, old_token, "Stale worker failure"
        )
        assert result is False

        db.session.refresh(outbox)
        assert outbox.lease_token == new_token
        assert outbox.last_error is None

        # The new worker must successfully finish fulfillment.
        assert _process_outbox_event(
            outbox_id,
            new_token
        ) is True

        db.session.refresh(outbox)

        user = User.query.filter_by(
            email=registered_user.email
        ).one()

        payments = Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).all()

        assert outbox.status == "completed"
        assert outbox.lease_token is None
        assert len(payments) == 1
        assert payments[0].status == "succeeded"
        assert user.plan_tier == "pro"


def test_retry_commit_failure_recovers_after_lease_expiry(
    app, db, registered_user, monkeypatch
):
    from app.tasks import (
        _claim_outbox_batch,
        _schedule_outbox_retry,
    )

    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )

        original_token = mark_event_processing(db, outbox_id)
        original_commit = db.session.commit

        def fail_commit():
            raise RuntimeError("Simulated database commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(db.session, "commit", fail_commit)

            with pytest.raises(
                RuntimeError, match="Simulated database commit failure"
            ):
                _schedule_outbox_retry(
                    outbox_id,
                    original_token,
                    "Simulated processing failure",
                )

        # The failed transaction must be rolled back.
        db.session.rollback()

        outbox = db.session.get(OutboxEvent, outbox_id)
        assert outbox.status == "processing"
        assert outbox.lease_token == original_token
        assert outbox.attempts == 1
        assert outbox.locked_until is not None

        # Simulate the original worker's lease expiring.
        outbox.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()

        # A new worker must be able to reclaim the event.
        claim = _claim_outbox_batch()

        assert claim is not None
        assert claim["id"] == outbox_id
        assert claim["lease_token"] != original_token

        db.session.refresh(outbox)
        assert outbox.status == "processing"
        assert outbox.lease_token == claim["lease_token"]
        assert outbox.attempts == 2



def test_paid_checkout_accepts_matching_metadata_user_id(
    app, db, registered_user
):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        checkout["metadata"]["user_id"] = str(registered_user.id)

        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        assert _process_outbox_event(outbox_id, lease_token) is True

        user = User.query.filter_by(email=registered_user.email).one()
        assert user.plan_tier == "pro"
        assert Payment.query.filter_by(
            stripe_checkout_session_id=checkout["id"]
        ).count() == 1


def test_paid_checkout_rejects_mismatched_metadata_user_id(
    app, db, registered_user
):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        checkout["metadata"]["user_id"] = str(registered_user.id + 1)

        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        with pytest.raises(ValueError, match="does not match account"):
            _process_outbox_event(outbox_id, lease_token)

        db.session.rollback()

        user = User.query.filter_by(email=registered_user.email).one()
        assert user.plan_tier != "pro"
        assert Payment.query.count() == 0


@pytest.mark.parametrize("invalid_user_id", ["abc", "", True])
def test_paid_checkout_rejects_invalid_metadata_user_id(
    app, db, registered_user, invalid_user_id
):
    with app.app_context():
        checkout = checkout_payload(registered_user.email)
        checkout["metadata"]["user_id"] = invalid_user_id

        outbox_id, _ = create_outbox_event(
            db, "checkout.session.completed", checkout
        )
        lease_token = mark_event_processing(db, outbox_id)

        with pytest.raises(ValueError, match="invalid user ID"):
            _process_outbox_event(outbox_id, lease_token)

        db.session.rollback()

        user = User.query.filter_by(email=registered_user.email).one()
        assert user.plan_tier != "pro"
        assert Payment.query.count() == 0
