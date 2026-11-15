
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event, local
from time import monotonic, sleep, time

import pytest
from sqlalchemy import text

from app.extensions import db
from app.services import invoice_service
from app.services.invoice_service import (
    create_invoice,
    get_invoice,
    issue_invoice,
    update_invoice,
    delete_invoice,
    InvoiceStateError,
)


def invoice_payload(customer_id):
    """Return a valid invoice payload for PostgreSQL tests."""

    return {
        "customer_id": customer_id,
        "issue_date": "2026-11-09",
        "due_date": "2026-12-09",
        "tax_rate": "18.00",
        "notes": "PostgreSQL concurrency test",
        "items": [
            {
                "description": "Backend development",
                "quantity": "2",
                "unit_price": "100.00",
            }
        ],
    }


@pytest.fixture
def postgres_invoice(postgres_app, postgres_workspace):
    """Create an invoice in the isolated PostgreSQL test database."""

    with postgres_app.app_context():
        invoice = create_invoice(
            postgres_workspace["tenant_id"],
            invoice_payload(postgres_workspace["customer_id"]),
        )

        return {
            "id": invoice.id,
            "tenant_id": postgres_workspace["tenant_id"],
        }


def wait_until_blocked(app, backend_pid, timeout=8):
    """Wait until PostgreSQL reports that a backend is blocked by a lock."""

    deadline = monotonic() + timeout

    with app.app_context():
        with db.engine.connect() as connection:
            while monotonic() < deadline:
                blocked = connection.execute(
                    text(
                        """
                        SELECT cardinality(pg_blocking_pids(:pid)) > 0
                        """
                    ),
                    {"pid": backend_pid},
                ).scalar_one()

                if blocked:
                    return True

                sleep(0.02)

    return False


def run_issuance_race(
    postgres_app,
    postgres_invoice,
    monkeypatch,
    competing_operation,
):
    """
    Hold an issuance transaction after its status update.

    Start a competing operation and verify PostgreSQL blocks it until
    the first transaction commits.
    """

    first_reached_commit = Event()
    release_first_transaction = Event()
    second_backend_pid = Queue()

    thread_state = local()

    original_commit = invoice_service._commit_invoice_changes

    def controlled_commit():
        if getattr(thread_state, "hold_transaction", False):
            first_reached_commit.set()

            if not release_first_transaction.wait(timeout=15):
                raise TimeoutError(
                    "Timed out waiting to release the first transaction"
                )

        return original_commit()

    monkeypatch.setattr(
        invoice_service,
        "_commit_invoice_changes",
        controlled_commit,
    )

    tenant_id = postgres_invoice["tenant_id"]
    invoice_id = postgres_invoice["id"]

    def first_worker():
        with postgres_app.app_context():
            thread_state.hold_transaction = True

            result = issue_invoice(
                tenant_id,
                invoice_id,
            )

            return result.status

    def second_worker():
        with postgres_app.app_context():
            backend_pid = db.session.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()

            second_backend_pid.put(backend_pid)

            return competing_operation(
                tenant_id,
                invoice_id,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_worker)

        assert first_reached_commit.wait(timeout=8), (
            "First transaction did not reach its commit point"
        )

        second_future = executor.submit(second_worker)

        try:
            backend_pid = second_backend_pid.get(timeout=8)

            assert wait_until_blocked(
                postgres_app,
                backend_pid,
            ), "Second transaction was not observed waiting on a PostgreSQL lock"

        finally:
            # Always release the first worker, including when an assertion fails.
            release_first_transaction.set()

        assert first_future.result(timeout=15) == "issued"

        with pytest.raises(InvoiceStateError):
            second_future.result(timeout=15)


def test_two_simultaneous_issuance_attempts(
    postgres_app,
    postgres_invoice,
    monkeypatch,
):
    """Only one issuance attempt should succeed."""

    run_issuance_race(
        postgres_app,
        postgres_invoice,
        monkeypatch,
        competing_operation=issue_invoice,
    )

    with postgres_app.app_context():
        invoice = get_invoice(
            postgres_invoice["tenant_id"],
            postgres_invoice["id"],
        )

        assert invoice.status == "issued"


def test_update_waiting_for_issuance_is_rejected(
    postgres_app,
    postgres_invoice,
    monkeypatch,
):
    """An update waiting behind issuance must not modify an issued invoice."""

    def competing_update(tenant_id, invoice_id):
        return update_invoice(
            tenant_id,
            invoice_id,
            {"notes": "Modified during issuance"},
        )

    run_issuance_race(
        postgres_app,
        postgres_invoice,
        monkeypatch,
        competing_operation=competing_update,
    )

    with postgres_app.app_context():
        invoice = get_invoice(
            postgres_invoice["tenant_id"],
            postgres_invoice["id"],
        )

        assert invoice.status == "issued"
        assert invoice.notes == "PostgreSQL concurrency test"


def test_delete_waiting_for_issuance_is_rejected(
    postgres_app,
    postgres_invoice,
    monkeypatch,
):
    """An invoice must not be deleted after a competing issuance commits."""

    run_issuance_race(
        postgres_app,
        postgres_invoice,
        monkeypatch,
        competing_operation=delete_invoice,
    )

    with postgres_app.app_context():
        invoice = get_invoice(
            postgres_invoice["tenant_id"],
            postgres_invoice["id"],
        )

        assert invoice.status == "issued"


def test_concurrent_invoice_number_allocation(
    postgres_app,
    postgres_workspace,
):
    """Concurrent invoice creation must allocate unique tenant numbers."""

    worker_count = 8

    start_barrier = __import__("threading").Barrier(worker_count)

    tenant_id = postgres_workspace["tenant_id"]
    customer_id = postgres_workspace["customer_id"]

    def create_worker():
        with postgres_app.app_context():
            start_barrier.wait(timeout=15)

            invoice = create_invoice(
                tenant_id,
                invoice_payload(customer_id),
            )

            return invoice.invoice_number

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(create_worker)
            for _ in range(worker_count)
        ]

        invoice_numbers = [
            future.result(timeout=30)
            for future in futures
        ]

    assert len(invoice_numbers) == worker_count
    assert len(set(invoice_numbers)) == worker_count

    expected_numbers = {
        f"INV-{number:06d}"
        for number in range(1, worker_count + 1)
    }

    assert set(invoice_numbers) == expected_numbers
