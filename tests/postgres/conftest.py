
import os
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import (
    User,
    Tenant,
    TenantMembership,
    Customer,
    Invoice,
)


@pytest.fixture(scope="session")
def postgres_app():
    """Create an app connected exclusively to the local test database."""

    database_url = os.getenv("ABYFLOW_TEST_DATABASE_URL")

    if not database_url:
        pytest.fail("ABYFLOW_TEST_DATABASE_URL is not configured")

    url = make_url(database_url)

    # Hard safety guard: never run these tests against another database.
    if (
        url.database != "abyflow_concurrency_test"
        or url.host not in ("localhost", "127.0.0.1")
        or url.drivername != "postgresql+psycopg2"
    ):
        pytest.fail("Refusing to run against an unexpected database")

    app = create_app(
        "development",
        test_config={
            "SQLALCHEMY_DATABASE_URI": database_url,
            "TESTING": True,
        },
    )

    with app.app_context():
        revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

        if revision != "b535855fde14":
            pytest.fail(
                f"Unexpected PostgreSQL migration revision: {revision}"
            )

    yield app

    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def postgres_workspace(postgres_app):
    """Create an isolated tenant and customer for one concurrency test."""

    unique = uuid.uuid4().hex

    with postgres_app.app_context():
        user = User(
            email=f"concurrency-{unique}@example.com",
            password_hash="test-only-password-hash",
        )

        db.session.add(user)
        db.session.flush()

        tenant = Tenant(
            name="Concurrency Test Workspace",
            slug=f"concurrency-{unique}",
        )

        db.session.add(tenant)
        db.session.flush()

        membership = TenantMembership(
            user_id=user.id,
            tenant_id=tenant.id,
            role="owner",
        )

        customer = Customer(
            tenant_id=tenant.id,
            name="Concurrency Test Customer",
            email=f"customer-{unique}@example.com",
        )

        db.session.add_all([membership, customer])
        db.session.commit()

        workspace = {
            "tenant_id": tenant.id,
            "customer_id": customer.id,
        }

        yield workspace

        db.session.execute(
            delete(Invoice).where(
                Invoice.tenant_id == tenant.id
            )
        )

        from app.models import InvoiceSequence

        db.session.execute(
            delete(InvoiceSequence).where(
                InvoiceSequence.tenant_id == tenant.id
            )
        )

        db.session.execute(
            delete(Customer).where(
                Customer.tenant_id == tenant.id
            )
        )

        db.session.execute(
            delete(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id
            )
        )

        db.session.execute(
            delete(Tenant).where(
                Tenant.id == tenant.id
            )
        )

        db.session.execute(
            delete(User).where(
                User.id == user.id
            )
        )

        db.session.commit()
