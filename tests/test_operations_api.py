
import pytest
from uuid import uuid4

from app.models import BillingEvent, OutboxEvent, OutboxReplayAudit


def create_failed_event(db):
    stripe_event_id = f"evt_test_{uuid4().hex}"

    billing = BillingEvent(
        stripe_event_id=stripe_event_id,
        event_type="checkout.session.completed",
        payload={
            "id": stripe_event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": f"cs_test_{uuid4().hex}",
                }
            },
        },
    )

    db.session.add(billing)
    db.session.flush()

    event = OutboxEvent(
        billing_event_id=billing.id,
        status="failed",
        attempts=5,
        last_error="Previous processing failure",
    )

    db.session.add(event)
    db.session.commit()

    return event.id


OPERATIONS_ENDPOINTS = [
    ("GET", "/api/ops/outbox/failed"),
    ("GET", "/api/ops/outbox/1"),
    ("GET", "/api/ops/outbox/replay-audit"),
    ("POST", "/api/ops/outbox/1/replay"),
]


def send_request(client, method, path, headers=None):
    if method == "POST":
        return client.post(
            path,
            headers=headers or {},
            json={"reason": "Investigated the original failure"},
        )

    return client.get(path, headers=headers or {})


@pytest.mark.parametrize(
    ("method", "path"),
    OPERATIONS_ENDPOINTS,
)
def test_operations_endpoints_require_authentication(
    client, method, path
):
    response = send_request(client, method, path)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    OPERATIONS_ENDPOINTS,
)
def test_tenant_owner_cannot_access_platform_operations(
    client, auth_headers, registered_user, method, path
):
    assert registered_user.platform_role == "user"
    assert registered_user.tenant_memberships[0].role == "owner"

    response = send_request(
        client,
        method,
        path,
        auth_headers,
    )

    assert response.status_code == 403


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_authorized_roles_can_use_all_operations_endpoints(
    client, db, auth_headers, registered_user, role
):
    registered_user.platform_role = role
    db.session.commit()

    event_id = create_failed_event(db)

    # List failed events.
    response = client.get(
        "/api/ops/outbox/failed",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["pagination"]["total"] == 1
    assert "payload" not in response.get_json()["events"][0]

    # Inspect the failed event.
    response = client.get(
        f"/api/ops/outbox/{event_id}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    event_data = response.get_json()["event"]
    assert event_data["id"] == event_id
    assert event_data["status"] == "failed"
    assert "payload" not in event_data

    # Replay the failed event.
    response = client.post(
        f"/api/ops/outbox/{event_id}/replay",
        headers=auth_headers,
        json={
            "reason": "Investigated the original failure",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["event"]["status"] == "pending"

    # Inspect the replay audit.
    response = client.get(
        "/api/ops/outbox/replay-audit",
        headers=auth_headers,
    )

    assert response.status_code == 200
    result = response.get_json()

    assert result["pagination"]["total"] == 1
    assert result["records"][0]["outbox_event_id"] == event_id
    assert result["records"][0]["operator_user_id"] == registered_user.id


def test_replay_rejects_invalid_reason(
    client, db, auth_headers, registered_user
):
    registered_user.platform_role = "operator"
    db.session.commit()

    event_id = create_failed_event(db)

    response = client.post(
        f"/api/ops/outbox/{event_id}/replay",
        headers=auth_headers,
        json={"reason": "short"},
    )

    assert response.status_code == 400

    event = db.session.get(OutboxEvent, event_id)

    assert event.status == "failed"
    assert event.attempts == 5
    assert OutboxReplayAudit.query.count() == 0


def test_replay_rejects_non_json_body(
    client, db, auth_headers, registered_user
):
    registered_user.platform_role = "operator"
    db.session.commit()

    event_id = create_failed_event(db)

    response = client.post(
        f"/api/ops/outbox/{event_id}/replay",
        headers=auth_headers,
        data="not-json",
        content_type="text/plain",
    )

    assert response.status_code == 400


def test_replay_returns_conflict_for_non_failed_event(
    client, db, auth_headers, registered_user
):
    registered_user.platform_role = "operator"
    db.session.commit()

    event_id = create_failed_event(db)

    event = db.session.get(OutboxEvent, event_id)
    event.status = "pending"
    db.session.commit()

    response = client.post(
        f"/api/ops/outbox/{event_id}/replay",
        headers=auth_headers,
        json={
            "reason": "Investigated the original failure",
        },
    )

    assert response.status_code == 409
    assert OutboxReplayAudit.query.count() == 0


def test_inspecting_missing_event_returns_404(
    client, db, auth_headers, registered_user
):
    registered_user.platform_role = "operator"
    db.session.commit()

    response = client.get(
        "/api/ops/outbox/999999",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_replaying_missing_event_returns_404(
    client, db, auth_headers, registered_user
):
    registered_user.platform_role = "operator"
    db.session.commit()

    response = client.post(
        "/api/ops/outbox/999999/replay",
        headers=auth_headers,
        json={
            "reason": "Investigated the original failure",
        },
    )

    assert response.status_code == 404
    assert OutboxReplayAudit.query.count() == 0
