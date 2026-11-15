
from flask import Blueprint, g, jsonify, request

from app.services.platform_authorization import platform_role_required
from app.services.outbox_operations import (
    OutboxNotFoundError,
    OutboxStateError,
    OutboxValidationError,
    get_failed_outbox_event,
    list_failed_outbox_events,
    list_outbox_replay_audit,
    replay_failed_outbox_event,
)


operations_bp = Blueprint('operations', __name__)

PLATFORM_OPERATORS = {'operator', 'admin'}


def serialize_outbox_event(event, billing_event=None):
    """Serialize safe outbox metadata. Never expose Stripe payloads."""
    billing_event = billing_event or event.billing_event

    return {
        'id': event.id,
        'billing_event_id': event.billing_event_id,
        'stripe_event_id': billing_event.stripe_event_id,
        'event_type': billing_event.event_type,
        'status': event.status,
        'attempts': event.attempts,
        'available_at': (
            event.available_at.isoformat()
            if event.available_at else None
        ),
        'locked_until': (
            event.locked_until.isoformat()
            if event.locked_until else None
        ),
        'last_error': (
            event.last_error[:500]
            if event.last_error else None
        ),
        'created_at': (
            event.created_at.isoformat()
            if event.created_at else None
        ),
        'completed_at': (
            event.completed_at.isoformat()
            if event.completed_at else None
        ),
    }


def serialize_replay_audit(record):
    """Serialize a replay audit record."""
    return {
        'id': record.id,
        'outbox_event_id': record.outbox_event_id,
        'stripe_event_id': record.stripe_event_id,
        'operator_user_id': record.operator_user_id,
        'reason': record.reason,
        'previous_attempts': record.previous_attempts,
        'replayed_at': (
            record.replayed_at.isoformat()
            if record.replayed_at else None
        ),
    }


def serialize_pagination(result):
    return {
        'total': result['total'],
        'page': result['page'],
        'per_page': result['per_page'],
        'pages': result['pages'],
    }


@operations_bp.route('/outbox/failed', methods=['GET'])
@platform_role_required(PLATFORM_OPERATORS)
def list_failed_outbox_route():
    """List failed outbox events."""
    try:
        result = list_failed_outbox_events(
            page=request.args.get('page', 1),
            per_page=request.args.get('per_page', 20),
        )

        return jsonify({
            'events': [
                serialize_outbox_event(event)
                for event in result['events']
            ],
            'pagination': serialize_pagination(result),
        }), 200

    except OutboxValidationError as error:
        return jsonify({'error': str(error)}), 400


@operations_bp.route('/outbox/<int:outbox_id>', methods=['GET'])
@platform_role_required(PLATFORM_OPERATORS)
def get_failed_outbox_route(outbox_id):
    """Inspect a failed outbox event."""
    try:
        event, billing_event = get_failed_outbox_event(outbox_id)

        return jsonify({
            'event': serialize_outbox_event(
                event,
                billing_event
            ),
        }), 200

    except OutboxNotFoundError as error:
        return jsonify({'error': str(error)}), 404


@operations_bp.route(
    '/outbox/<int:outbox_id>/replay',
    methods=['POST']
)
@platform_role_required(PLATFORM_OPERATORS)
def replay_failed_outbox_route(outbox_id):
    """Replay a failed event and atomically record its audit."""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            'error': 'Request body must be a JSON object'
        }), 400

    try:
        event, audit = replay_failed_outbox_event(
            outbox_id=outbox_id,
            operator_user_id=g.current_platform_user.id,
            reason=data.get('reason'),
        )

        return jsonify({
            'message': 'Outbox event queued for replay',
            'event': {
                'id': event.id,
                'status': event.status,
                'attempts': event.attempts,
            },
            'audit': serialize_replay_audit(audit),
        }), 200

    except OutboxNotFoundError as error:
        return jsonify({'error': str(error)}), 404

    except OutboxStateError as error:
        return jsonify({'error': str(error)}), 409

    except OutboxValidationError as error:
        return jsonify({'error': str(error)}), 400


@operations_bp.route(
    '/outbox/replay-audit',
    methods=['GET']
)
@platform_role_required(PLATFORM_OPERATORS)
def list_outbox_replay_audit_route():
    """List replay audit history."""
    try:
        result = list_outbox_replay_audit(
            page=request.args.get('page', 1),
            per_page=request.args.get('per_page', 20),
            outbox_event_id=request.args.get('outbox_event_id'),
        )

        return jsonify({
            'records': [
                serialize_replay_audit(record)
                for record in result['records']
            ],
            'pagination': serialize_pagination(result),
        }), 200

    except OutboxValidationError as error:
        return jsonify({'error': str(error)}), 400
