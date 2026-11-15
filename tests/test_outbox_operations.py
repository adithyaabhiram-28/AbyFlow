
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.extensions import db
from app.models import (
    BillingEvent,
    OutboxEvent,
    OutboxReplayAudit,
)
from app.services.outbox_operations import (
    OutboxNotFoundError,
    OutboxStateError,
    OutboxValidationError,
    get_failed_outbox_event,
    list_failed_outbox_events,
    list_outbox_replay_audit,
    replay_failed_outbox_event,
)

def create_failed_event():
    """Create a billing event and a failed outbox event."""
    stripe_event_id = f"evt_test_{uuid4().hex}"

    billing = BillingEvent(
        stripe_event_id=stripe_event_id,
        event_type="checkout.session.completed",
        payload={
            "id": stripe_event_id,
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_example"}},
        },
    )

    db.session.add(billing)
    db.session.flush()

    outbox = OutboxEvent(
        billing_event_id=billing.id,
        status="failed",
        attempts=5,
        last_error="Previous processing failed",
    )

    db.session.add(outbox)
    db.session.commit()

    return outbox.id, billing.id, stripe_event_id

def test_successful_replay_resets_event_and_creates_audit(
    app, db, registered_user
):
    with app.app_context():
        outbox_id, _, stripe_event_id = create_failed_event()

        event, audit = replay_failed_outbox_event(
            outbox_id=outbox_id,
            operator_user_id=registered_user.id,
            reason="Verified the original processing failure",
        )

        db.session.refresh(event)
        db.session.refresh(audit)

        assert event.status == "pending"
        assert event.attempts == 0
        assert event.available_at is not None

        available_at = event.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=timezone.utc)

        assert available_at <= datetime.now(timezone.utc)
        assert event.locked_until is None
        assert event.lease_token is None
        assert event.last_error is None
        assert event.completed_at is None

        assert audit.outbox_event_id == outbox_id
        assert audit.stripe_event_id == stripe_event_id
        assert audit.operator_user_id == registered_user.id
        assert audit.previous_attempts == 5
        assert audit.reason == "Verified the original processing failure"
        assert audit.replayed_at is not None

        assert OutboxReplayAudit.query.count() == 1

def test_replay_preserves_previous_attempt_count_in_audit(
    app, db, registered_user
):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        event = db.session.get(OutboxEvent, outbox_id)
        event.attempts = 8
        db.session.commit()

        replay_failed_outbox_event(
            outbox_id,
            registered_user.id,
            "Retry after investigating the failure",
        )

        audit = OutboxReplayAudit.query.one()
        event = db.session.get(OutboxEvent, outbox_id)

        assert audit.previous_attempts == 8
        assert event.attempts == 0

@pytest.mark.parametrize(
    "reason",
    [
        None,
        123,
        "",
        "short",
        "x" * 501,
    ],
)
def test_invalid_replay_reason_is_rejected(
    app, db, registered_user, reason
):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        with pytest.raises(OutboxValidationError):
            replay_failed_outbox_event(
                outbox_id,
                registered_user.id,
                reason,
            )

        event = db.session.get(OutboxEvent, outbox_id)

        assert event.status == "failed"
        assert event.attempts == 5
        assert OutboxReplayAudit.query.count() == 0

def test_boolean_operator_id_is_rejected(app, db):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        with pytest.raises(OutboxValidationError):
            replay_failed_outbox_event(
                outbox_id,
                True,
                "Investigated the failed processing attempt",
            )

        assert OutboxReplayAudit.query.count() == 0

def test_missing_outbox_event_raises_not_found(
    app, db, registered_user
):
    with app.app_context():
        with pytest.raises(OutboxNotFoundError):
            replay_failed_outbox_event(
                999999,
                registered_user.id,
                "Investigated the failed processing attempt",
            )

        assert OutboxReplayAudit.query.count() == 0

def test_pending_event_cannot_be_replayed(
    app, db, registered_user
):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        event = db.session.get(OutboxEvent, outbox_id)
        event.status = "pending"
        db.session.commit()

        with pytest.raises(OutboxStateError):
            replay_failed_outbox_event(
                outbox_id,
                registered_user.id,
                "Investigated the failed processing attempt",
            )

        assert OutboxReplayAudit.query.count() == 0

def test_completed_event_cannot_be_replayed(
    app, db, registered_user
):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        event = db.session.get(OutboxEvent, outbox_id)
        event.status = "completed"
        db.session.commit()

        with pytest.raises(OutboxStateError):
            replay_failed_outbox_event(
                outbox_id,
                registered_user.id,
                "Investigated the failed processing attempt",
            )

        assert OutboxReplayAudit.query.count() == 0

def test_second_replay_is_rejected_after_first_replay(
    app, db, registered_user
):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        replay_failed_outbox_event(
            outbox_id,
            registered_user.id,
            "First replay after investigating failure",
        )

        with pytest.raises(OutboxStateError):
            replay_failed_outbox_event(
                outbox_id,
                registered_user.id,
                "Second replay should not be permitted",
            )

        assert OutboxReplayAudit.query.count() == 1

        event = db.session.get(OutboxEvent, outbox_id)
        assert event.status == "pending"

def test_replay_rolls_back_event_and_audit_on_commit_failure(
    app, db, registered_user, monkeypatch
):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        def fail_commit():
            raise RuntimeError("Simulated database commit failure")

        monkeypatch.setattr(db.session, "commit", fail_commit)

        with pytest.raises(
            RuntimeError,
            match="Simulated database commit failure",
        ):
            replay_failed_outbox_event(
                outbox_id,
                registered_user.id,
                "Investigated the failed processing attempt",
            )

        event = db.session.get(OutboxEvent, outbox_id)

        assert event.status == "failed"
        assert event.attempts == 5
        assert OutboxReplayAudit.query.count() == 0

def test_list_failed_events_returns_only_failed_events(
    app, db
):
    with app.app_context():
        failed_id, _, _ = create_failed_event()

        pending_id, _, _ = create_failed_event()
        pending = db.session.get(OutboxEvent, pending_id)
        pending.status = "pending"
        db.session.commit()

        result = list_failed_outbox_events()

        assert result["total"] == 1
        assert len(result["events"]) == 1
        assert result["events"][0].id == failed_id

def test_get_failed_event_returns_billing_metadata(app, db):
    with app.app_context():
        outbox_id, billing_id, stripe_event_id = create_failed_event()

        event, billing = get_failed_outbox_event(outbox_id)

        assert event.id == outbox_id
        assert billing.id == billing_id
        assert billing.stripe_event_id == stripe_event_id

def test_get_failed_event_rejects_non_failed_event(app, db):
    with app.app_context():
        outbox_id, _, _ = create_failed_event()

        event = db.session.get(OutboxEvent, outbox_id)
        event.status = "pending"
        db.session.commit()

        with pytest.raises(OutboxNotFoundError):
            get_failed_outbox_event(outbox_id)

def test_replay_audit_can_be_filtered_by_event(
    app, db, registered_user
):
    with app.app_context():
        first_id, _, _ = create_failed_event()
        second_id, _, _ = create_failed_event()

        replay_failed_outbox_event(
            first_id,
            registered_user.id,
            "Investigated first failed processing event",
        )

        replay_failed_outbox_event(
            second_id,
            registered_user.id,
            "Investigated second failed processing event",
        )

        result = list_outbox_replay_audit(
            outbox_event_id=first_id
        )

        assert result["total"] == 1
        assert len(result["records"]) == 1
        assert result["records"][0].outbox_event_id == first_id

@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 20),
        (1, 0),
        (1, 101),
        ("invalid", 20),
        (1, "invalid"),
    ],
)
def test_invalid_pagination_is_rejected(
    app, page, per_page
):
    with app.app_context():
        with pytest.raises(OutboxValidationError):
            list_failed_outbox_events(page, per_page)

        with pytest.raises(OutboxValidationError):
            list_outbox_replay_audit(page, per_page)