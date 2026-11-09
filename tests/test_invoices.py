
from decimal import Decimal

import pytest

from app.models import Invoice, InvoiceItem, InvoiceSequence
from app.services.invoice_service import (
    create_invoice,
    get_invoice,
    calculate_invoice_totals,
    InvoiceValidationError,
    InvoiceCustomerNotFoundError,
)

from app.services.invoice_service import (
    update_invoice,
    delete_invoice,
    issue_invoice,
    list_invoices,
    InvoiceNotFoundError,
    InvoiceStateError,
)


def invoice_payload(customer_id, **overrides):
    """Build a valid invoice payload for tests."""

    payload = {
        "customer_id": customer_id,
        "issue_date": "2026-11-08",
        "due_date": "2026-12-08",
        "tax_rate": "18.00",
        "notes": "Test invoice",
        "items": [
            {
                "description": "Backend development",
                "quantity": "10",
                "unit_price": "49.99",
            },
            {
                "description": "API integration",
                "quantity": "5",
                "unit_price": "29.99",
            },
        ],
    }

    payload.update(overrides)
    return payload


def test_create_invoice_success(
    app,
    db,
    registered_user,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    assert invoice.id is not None
    assert invoice.tenant_id == tenant_id
    assert invoice.customer_id == registered_customer.id
    assert invoice.invoice_number == "INV-000001"
    assert invoice.status == "draft"
    assert invoice.currency == "USD"
    assert invoice.tax_rate == Decimal("18.00")
    assert invoice.subtotal == Decimal("649.85")
    assert invoice.tax_amount == Decimal("116.97")
    assert invoice.total == Decimal("766.82")
    assert len(invoice.items) == 2


def test_get_invoice_success(
    app,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    created = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    retrieved = get_invoice(
        tenant_id,
        created.id,
    )

    assert retrieved.id == created.id
    assert retrieved.invoice_number == created.invoice_number
    assert len(retrieved.items) == 2


def test_invoice_numbering_increments(
    app,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    first = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    second = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    assert first.invoice_number == "INV-000001"
    assert second.invoice_number == "INV-000002"


def test_invoice_calculation_with_tax():
    items = [
        {
            "description": "Development",
            "quantity": "10",
            "unit_price": "49.99",
        },
        {
            "description": "Integration",
            "quantity": "5",
            "unit_price": "29.99",
        },
    ]

    totals = calculate_invoice_totals(
        items,
        tax_rate="18.00",
    )

    assert totals["subtotal"] == Decimal("649.85")
    assert totals["tax_amount"] == Decimal("116.97")
    assert totals["total"] == Decimal("766.82")


def test_invoice_calculation_without_tax():
    items = [
        {
            "description": "Development",
            "quantity": "2",
            "unit_price": "100.00",
        }
    ]

    totals = calculate_invoice_totals(items)

    assert totals["subtotal"] == Decimal("200.00")
    assert totals["tax_amount"] == Decimal("0.00")
    assert totals["total"] == Decimal("200.00")


@pytest.mark.parametrize(
    "tax_rate",
    ["-1", "100.01", "101", "NaN", "Infinity"],
)
def test_invalid_tax_rate_rejected(tax_rate):
    items = [
        {
            "description": "Development",
            "quantity": "1",
            "unit_price": "100.00",
        }
    ]

    with pytest.raises(InvoiceValidationError):
        calculate_invoice_totals(
            items,
            tax_rate=tax_rate,
        )


@pytest.mark.parametrize(
    "customer_id",
    [0, -1, True, 1.5, "1.5", None],
)
def test_invalid_customer_id_rejected(
    app,
    registered_customer,
    customer_id,
):
    with pytest.raises(
        (InvoiceValidationError, InvoiceCustomerNotFoundError)
    ):
        create_invoice(
            registered_customer.tenant_id,
            invoice_payload(customer_id),
        )


def test_cross_tenant_customer_rejected(
    app,
    registered_customer,
    second_tenant,
):
    with pytest.raises(InvoiceCustomerNotFoundError):
        create_invoice(
            registered_customer.tenant_id,
            invoice_payload(
                second_tenant["customer"].id
            ),
        )


def test_invoice_number_sequence_is_tenant_scoped(
    app,
    registered_customer,
    second_tenant,
):
    first_tenant_invoice = create_invoice(
        registered_customer.tenant_id,
        invoice_payload(registered_customer.id),
    )

    second_tenant_invoice = create_invoice(
        second_tenant["tenant"].id,
        invoice_payload(second_tenant["customer"].id),
    )

    assert first_tenant_invoice.invoice_number == "INV-000001"
    assert second_tenant_invoice.invoice_number == "INV-000001"


# ============================================================
# Invoice update tests
# ============================================================

def test_update_invoice_notes(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    updated = update_invoice(
        tenant_id,
        invoice.id,
        {"notes": "Updated billing notes"},
    )

    assert updated.notes == "Updated billing notes"
    assert updated.total == Decimal("766.82")


def test_replace_items_retains_existing_tax_rate(
    app,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    updated = update_invoice(
        tenant_id,
        invoice.id,
        {
            "items": [
                {
                    "description": "New development work",
                    "quantity": "2",
                    "unit_price": "100.00",
                }
            ]
        },
    )

    assert updated.tax_rate == Decimal("18.00")
    assert updated.subtotal == Decimal("200.00")
    assert updated.tax_amount == Decimal("36.00")
    assert updated.total == Decimal("236.00")
    assert len(updated.items) == 1


def test_change_tax_rate_without_replacing_items(
    app,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    updated = update_invoice(
        tenant_id,
        invoice.id,
        {"tax_rate": "10.00"},
    )

    assert updated.tax_rate == Decimal("10.00")
    assert updated.subtotal == Decimal("649.85")
    assert updated.tax_amount == Decimal("64.99")
    assert updated.total == Decimal("714.84")
    assert len(updated.items) == 2


def test_update_items_and_tax_rate_together(
    app,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    updated = update_invoice(
        tenant_id,
        invoice.id,
        {
            "tax_rate": "5.00",
            "items": [
                {
                    "description": "Consulting",
                    "quantity": "3",
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert updated.subtotal == Decimal("300.00")
    assert updated.tax_rate == Decimal("5.00")
    assert updated.tax_amount == Decimal("15.00")
    assert updated.total == Decimal("315.00")


def test_update_invoice_customer(
    app,
    db,
    registered_customer,
):
    from app.models import Customer

    tenant_id = registered_customer.tenant_id

    new_customer = Customer(
        tenant_id=tenant_id,
        name="New Customer",
        email="newcustomer@example.com",
    )

    db.session.add(new_customer)
    db.session.commit()

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    updated = update_invoice(
        tenant_id,
        invoice.id,
        {"customer_id": str(new_customer.id)},
    )

    assert updated.customer_id == new_customer.id


def test_update_invoice_rejects_empty_payload(
    app,
    registered_customer,
):
    invoice = create_invoice(
        registered_customer.tenant_id,
        invoice_payload(registered_customer.id),
    )

    with pytest.raises(InvoiceValidationError):
        update_invoice(
            registered_customer.tenant_id,
            invoice.id,
            {},
        )


def test_update_invoice_rejects_invalid_tax_rate(
    app,
    registered_customer,
):
    invoice = create_invoice(
        registered_customer.tenant_id,
        invoice_payload(registered_customer.id),
    )

    with pytest.raises(InvoiceValidationError):
        update_invoice(
            registered_customer.tenant_id,
            invoice.id,
            {"tax_rate": "150"},
        )


# ============================================================
# Invoice lifecycle tests
# ============================================================

def test_delete_draft_invoice(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    delete_invoice(tenant_id, invoice.id)

    with pytest.raises(InvoiceNotFoundError):
        get_invoice(tenant_id, invoice.id)


def test_issue_draft_invoice(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    issued = issue_invoice(tenant_id, invoice.id)

    assert issued.status == "issued"
    assert issued.id == invoice.id


def test_cannot_issue_invoice_twice(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    issue_invoice(tenant_id, invoice.id)

    with pytest.raises(InvoiceStateError):
        issue_invoice(tenant_id, invoice.id)


def test_cannot_update_issued_invoice(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    issue_invoice(tenant_id, invoice.id)

    with pytest.raises(InvoiceStateError):
        update_invoice(
            tenant_id,
            invoice.id,
            {"notes": "Not allowed"},
        )


def test_cannot_delete_issued_invoice(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    invoice = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    issue_invoice(tenant_id, invoice.id)

    with pytest.raises(InvoiceStateError):
        delete_invoice(tenant_id, invoice.id)


# ============================================================
# Invoice pagination tests
# ============================================================

def test_list_invoices(app, registered_customer):
    tenant_id = registered_customer.tenant_id

    create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    result = list_invoices(tenant_id)

    assert result["total"] == 2
    assert len(result["invoices"]) == 2
    assert result["page"] == 1
    assert result["per_page"] == 20
    assert result["pages"] == 1


def test_list_invoices_with_status_filter(
    app,
    registered_customer,
):
    tenant_id = registered_customer.tenant_id

    first = create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    issue_invoice(tenant_id, first.id)

    create_invoice(
        tenant_id,
        invoice_payload(registered_customer.id),
    )

    result = list_invoices(
        tenant_id,
        status="draft",
    )

    assert result["total"] == 1
    assert result["invoices"][0].status == "draft"


@pytest.mark.parametrize(
    "page, per_page",
    [
        (0, 20),
        (-1, 20),
        (1.5, 20),
        (True, 20),
        (1, 0),
        (1, 101),
        (1, 2.5),
        (1, True),
    ],
)
def test_invalid_pagination_rejected(
    app,
    registered_customer,
    page,
    per_page,
):
    with pytest.raises(InvoiceValidationError):
        list_invoices(
            registered_customer.tenant_id,
            page=page,
            per_page=per_page,
        )


@pytest.mark.parametrize(
    "status",
    [
        "unknown",
        "",
        [],
        {},
        True,
    ],
)
def test_invalid_status_filter_rejected(
    app,
    registered_customer,
    status,
):
    with pytest.raises(InvoiceValidationError):
        list_invoices(
            registered_customer.tenant_id,
            status=status,
        )

def test_create_invoice_rejects_more_than_100_items(
    app,
    registered_user,
    registered_customer,
):
    from app.services.invoice_service import (
        create_invoice,
        InvoiceValidationError,
    )

    with app.app_context():
        items = [
            {
                "description": f"Item {i}",
                "quantity": "1",
                "unit_price": "10.00",
            }
            for i in range(101)
        ]

        with pytest.raises(
            InvoiceValidationError,
            match="cannot contain more than 100 items",
        ):
            create_invoice(
                registered_customer.tenant_id,
                {
                    "customer_id": registered_customer.id,
                    "issue_date": "2026-11-09",
                    "due_date": "2026-12-09",
                    "items": items,
                },
            )


def test_create_invoice_accepts_exactly_100_items(
    app,
    registered_user,
    registered_customer,
):
    from app.services.invoice_service import create_invoice

    with app.app_context():
        items = [
            {
                "description": f"Item {i}",
                "quantity": "1",
                "unit_price": "10.00",
            }
            for i in range(100)
        ]

        invoice = create_invoice(
            registered_customer.tenant_id,
            {
                "customer_id": registered_customer.id,
                "issue_date": "2026-11-09",
                "due_date": "2026-12-09",
                "items": items,
            },
        )

        assert len(invoice.items) == 100
        assert invoice.total == Decimal("1000.00")