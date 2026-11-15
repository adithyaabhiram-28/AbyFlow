
from datetime import datetime, timezone

from sqlalchemy import select, func

from app.extensions import db
from app.models import (
    BillingEvent,
    OutboxEvent,
    OutboxReplayAudit,
)


class OutboxOperationError(Exception):
    """Base exception for outbox operations."""


class OutboxNotFoundError(OutboxOperationError):
    """Outbox event does not exist."""


class OutboxStateError(OutboxOperationError):
    """Outbox event is not in a state that permits the operation."""


class OutboxValidationError(OutboxOperationError):
    """Outbox operation input is invalid."""


def _parse_pagination(page, per_page):
    """Validate and normalize pagination parameters."""
    try:
        page = int(page)
        per_page = int(per_page)
    except (TypeError, ValueError):
        raise OutboxValidationError(
            'page and per_page must be integers'
        )

    if page < 1:
        raise OutboxValidationError('page must be at least 1')

    if per_page < 1 or per_page > 100:
        raise OutboxValidationError(
            'per_page must be between 1 and 100'
        )

    return page, per_page


def list_failed_outbox_events(page=1, per_page=20):
    """Return paginated failed events without exposing Stripe payloads."""
    page, per_page = _parse_pagination(page, per_page)

    query = (
        db.session.query(OutboxEvent)
        .filter(OutboxEvent.status == 'failed')
    )

    total = query.count()

    events = (
        query
        .order_by(
            OutboxEvent.created_at.desc(),
            OutboxEvent.id.desc()
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        'events': events,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
    }


def get_failed_outbox_event(outbox_id):
    """Retrieve one failed event and its billing metadata."""
    result = (
        db.session.query(OutboxEvent, BillingEvent)
        .join(
            BillingEvent,
            OutboxEvent.billing_event_id == BillingEvent.id
        )
        .filter(
            OutboxEvent.id == outbox_id,
            OutboxEvent.status == 'failed'
        )
        .one_or_none()
    )

    if result is None:
        raise OutboxNotFoundError(
            'Failed outbox event not found'
        )

    return result


def replay_failed_outbox_event(
    outbox_id,
    operator_user_id,
    reason
):
    """
    Atomically audit and reset a failed outbox event.

    The row lock prevents concurrent operators from replaying
    the same failed event successfully.
    """
    if not isinstance(reason, str):
        raise OutboxValidationError(
            'Replay reason must be a string'
        )

    reason = reason.strip()

    if len(reason) < 10 or len(reason) > 500:
        raise OutboxValidationError(
            'Replay reason must contain 10 to 500 characters'
        )

    if (
        isinstance(operator_user_id, bool)
        or not isinstance(operator_user_id, int)
        or operator_user_id <= 0
    ):
        raise OutboxValidationError(
            'Invalid operator user ID'
        )

    try:
        event = db.session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.id == outbox_id)
            .with_for_update()
        ).scalar_one_or_none()

        if event is None:
            raise OutboxNotFoundError(
                'Outbox event not found'
            )

        if event.status != 'failed':
            raise OutboxStateError(
                'Only failed outbox events can be replayed'
            )

        billing_event = db.session.get(
            BillingEvent,
            event.billing_event_id
        )

        if billing_event is None:
            raise OutboxStateError(
                'Associated billing event not found'
            )

        audit = OutboxReplayAudit(
            outbox_event_id=event.id,
            stripe_event_id=billing_event.stripe_event_id,
            operator_user_id=operator_user_id,
            reason=reason,
            previous_attempts=event.attempts,
        )

        db.session.add(audit)

        event.status = 'pending'
        event.attempts = 0
        event.available_at = datetime.now(timezone.utc)
        event.locked_until = None
        event.lease_token = None
        event.last_error = None
        event.completed_at = None

        db.session.commit()

        return event, audit

    except OutboxOperationError:
        db.session.rollback()
        raise

    except Exception:
        db.session.rollback()
        raise


def list_outbox_replay_audit(
    page=1,
    per_page=20,
    outbox_event_id=None
):
    """Return paginated replay audit records."""
    page, per_page = _parse_pagination(page, per_page)

    query = db.session.query(OutboxReplayAudit)

    if outbox_event_id is not None:
        if isinstance(outbox_event_id, bool):
            raise OutboxValidationError(
                'outbox_event_id must be a positive integer'
            )

        try:
            outbox_event_id = int(outbox_event_id)
        except (TypeError, ValueError):
            raise OutboxValidationError(
                'outbox_event_id must be a positive integer'
            )

        if outbox_event_id < 1:
            raise OutboxValidationError(
                'outbox_event_id must be a positive integer'
            )

        query = query.filter(
            OutboxReplayAudit.outbox_event_id == outbox_event_id
        )

    total = query.count()

    records = (
        query
        .order_by(
            OutboxReplayAudit.replayed_at.desc(),
            OutboxReplayAudit.id.desc()
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        'records': records,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
    }
