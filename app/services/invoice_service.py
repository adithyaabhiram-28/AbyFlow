
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    Customer,
    Invoice,
    InvoiceItem,
    InvoiceSequence,
)


class InvoiceError(Exception):
    """Base exception for invoice operations."""


class InvoiceNotFoundError(InvoiceError):
    """Invoice does not exist within the requested tenant."""


class InvoiceValidationError(InvoiceError):
    """Invoice input failed validation."""


class InvoiceCustomerNotFoundError(InvoiceError):
    """Customer does not belong to the requested tenant."""


class InvoiceStateError(InvoiceError):
    """Requested operation is invalid for the invoice state."""


class InvoiceNumberingError(InvoiceError):
    """Invoice number allocation failed."""


CENT = Decimal("0.01")
THREE_DECIMALS = Decimal("0.001")
ZERO = Decimal("0.00")
ONE_HUNDRED = Decimal("100")

MAX_AMOUNT = Decimal("9999999999.99")
MAX_QUANTITY = Decimal("999999999.999")
MAX_UNIT_PRICE = Decimal("9999999999.99")
MAX_INVOICE_ITEMS = 100

ALLOWED_INVOICE_FIELDS = {
    "customer_id",
    "issue_date",
    "due_date",
    "items",
    "tax_rate",
    "notes",
}

ALLOWED_ITEM_FIELDS = {
    "description",
    "quantity",
    "unit_price",
}

ALLOWED_STATUSES = {
    "draft",
    "issued",
    "paid",
    "overdue",
    "cancelled",
}


def _parse_decimal(value, field, scale):
    """Convert and validate decimal input safely."""

    if isinstance(value, bool) or value is None:
        raise InvoiceValidationError(
            f"{field} must be a valid number"
        )

    if not isinstance(value, (str, int, float, Decimal)):
        raise InvoiceValidationError(
            f"{field} must be a valid number"
        )

    try:
        number = Decimal(str(value))

        if not number.is_finite():
            raise InvoiceValidationError(
                f"{field} must be finite"
            )

        if number < 0:
            raise InvoiceValidationError(
                f"{field} cannot be negative"
            )

        if scale == 2:
            quantum = CENT
        elif scale == 3:
            quantum = THREE_DECIMALS
        else:
            raise ValueError("Unsupported decimal scale")

        if number != number.quantize(quantum):
            raise InvoiceValidationError(
                f"{field} supports at most {scale} decimal places"
            )

        return number

    except InvalidOperation:
        raise InvoiceValidationError(
            f"{field} exceeds the supported numeric precision"
        )


def _parse_date(value, field):
    """Accept ISO-8601 calendar dates."""

    if isinstance(value, datetime):
        raise InvoiceValidationError(
            f"{field} must use YYYY-MM-DD format"
        )

    if isinstance(value, date):
        return value

    if not isinstance(value, str):
        raise InvoiceValidationError(
            f"{field} must use YYYY-MM-DD format"
        )

    try:
        parsed = date.fromisoformat(value)

        if parsed.isoformat() != value:
            raise ValueError

        return parsed

    except ValueError:
        raise InvoiceValidationError(
            f"{field} must use YYYY-MM-DD format"
        )


def _validate_notes(value):
    if value is None:
        return None

    if not isinstance(value, str):
        raise InvoiceValidationError(
            "Notes must be a string"
        )

    value = value.strip()

    if len(value) > 10000:
        raise InvoiceValidationError(
            "Notes cannot exceed 10000 characters"
        )

    return value or None


def _validate_customer(tenant_id, customer_id):
    """Ensure the customer ID is valid and belongs to the tenant."""

    if isinstance(customer_id, bool):
        raise InvoiceValidationError(
            "Customer ID must be a positive integer"
        )

    if isinstance(customer_id, int):
        normalized_id = customer_id

    elif isinstance(customer_id, str):
        value = customer_id.strip()

        if not value.isdigit():
            raise InvoiceValidationError(
                "Customer ID must be a positive integer"
            )

        normalized_id = int(value)

    else:
        raise InvoiceValidationError(
            "Customer ID must be a positive integer"
        )

    if normalized_id <= 0:
        raise InvoiceValidationError(
            "Customer ID must be a positive integer"
        )

    customer = db.session.execute(
        select(Customer).where(
            Customer.id == normalized_id,
            Customer.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    if customer is None:
        raise InvoiceCustomerNotFoundError(
            "Customer not found in this tenant"
        )

    return customer


def _normalize_items(items):
    """Validate invoice items and calculate their individual totals."""

    if not isinstance(items, list) or not items:
        raise InvoiceValidationError(
            "At least one invoice item is required"
        )

    if len(items) > MAX_INVOICE_ITEMS:
        raise InvoiceValidationError(
            f"An invoice cannot contain more than {MAX_INVOICE_ITEMS} items"
        )

    normalized_items = []

    for index, item in enumerate(items):

        if not isinstance(item, dict):
            raise InvoiceValidationError(
                f"Item {index + 1} must be an object"
            )

        unexpected = set(item) - ALLOWED_ITEM_FIELDS

        if unexpected:
            raise InvoiceValidationError(
                f"Unsupported item fields: {', '.join(sorted(unexpected))}"
            )

        description = item.get("description")

        if not isinstance(description, str):
            raise InvoiceValidationError(
                f"Item {index + 1}: description is required"
            )

        description = description.strip()

        if not description or len(description) > 255:
            raise InvoiceValidationError(
                f"Item {index + 1}: description must contain 1-255 characters"
            )

        quantity = _parse_decimal(
            item.get("quantity"),
            f"Item {index + 1} quantity",
            3,
        )

        unit_price = _parse_decimal(
            item.get("unit_price"),
            f"Item {index + 1} unit price",
            2,
        )

        if quantity <= 0:
            raise InvoiceValidationError(
                f"Item {index + 1}: quantity must be greater than zero"
            )

        if quantity > MAX_QUANTITY:
            raise InvoiceValidationError(
                f"Item {index + 1}: quantity exceeds the supported limit"
            )

        if unit_price > MAX_UNIT_PRICE:
            raise InvoiceValidationError(
                f"Item {index + 1}: unit price exceeds the supported limit"
            )

        try:
            line_total = (
                quantity * unit_price
            ).quantize(
                CENT,
                rounding=ROUND_HALF_UP,
            )

        except InvalidOperation:
            raise InvoiceValidationError(
                f"Item {index + 1}: line total exceeds the supported limit"
            )

        if line_total > MAX_AMOUNT:
            raise InvoiceValidationError(
                f"Item {index + 1}: line total exceeds the supported limit"
            )

        normalized_items.append({
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    return normalized_items


def _calculate_totals(items, tax_rate):
    """Calculate invoice totals using Decimal arithmetic."""

    tax_rate = _parse_decimal(
        tax_rate,
        "Tax rate",
        2,
    )

    if tax_rate > ONE_HUNDRED:
        raise InvoiceValidationError(
            "Tax rate cannot exceed 100%"
        )

    try:
        subtotal = sum(
            (item["line_total"] for item in items),
            ZERO,
        ).quantize(CENT)

        if subtotal > MAX_AMOUNT:
            raise InvoiceValidationError(
                "Invoice subtotal exceeds the supported limit"
            )

        tax_amount = (
            subtotal * tax_rate / ONE_HUNDRED
        ).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )

        total = (
            subtotal + tax_amount
        ).quantize(CENT)

    except InvalidOperation:
        raise InvoiceValidationError(
            "Invoice calculation exceeds the supported numeric precision"
        )

    if total > MAX_AMOUNT:
        raise InvoiceValidationError(
            "Invoice total exceeds the supported limit"
        )

    return {
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total": total,
    }


def calculate_invoice_totals(items, tax_rate=ZERO):
    """
    Public calculation helper.

    Returns Decimal subtotal, tax amount and total.
    Does not modify the database.
    """

    normalized_items = _normalize_items(items)

    return _calculate_totals(
        normalized_items,
        tax_rate,
    )


def _allocate_invoice_number(tenant_id):
    """
    Atomically allocate a tenant-specific invoice number.

    next_number represents the next available number.

    Initial insert:
        Stores 2 and returns 2 -> allocated number 1.

    Subsequent requests:
        Atomically increments the existing counter.

    Allocation occurs inside the caller's transaction.
    """

    dialect = db.session.get_bind().dialect.name

    if dialect == "sqlite":
        insert_statement = sqlite_insert(InvoiceSequence)

    elif dialect == "postgresql":
        insert_statement = postgres_insert(InvoiceSequence)

    else:
        raise InvoiceNumberingError(
            f"Unsupported database dialect: {dialect}"
        )

    statement = (
        insert_statement
        .values(
            tenant_id=tenant_id,
            next_number=2,
        )
        .on_conflict_do_update(
            index_elements=[InvoiceSequence.tenant_id],
            set_={
                "next_number": InvoiceSequence.next_number + 1
            },
        )
        .returning(InvoiceSequence.next_number)
    )

    next_number = db.session.execute(
        statement
    ).scalar_one()

    allocated_number = next_number - 1

    return f"INV-{allocated_number:06d}"


def _commit_invoice_changes():
    try:
        db.session.commit()

    except Exception:
        db.session.rollback()
        raise


def create_invoice(tenant_id, data):
    """Create a draft invoice inside a tenant."""

    try:
        if not isinstance(data, dict):
            raise InvoiceValidationError(
                "Invalid JSON request body"
            )

        unexpected = set(data) - ALLOWED_INVOICE_FIELDS

        if unexpected:
            raise InvoiceValidationError(
                f"Unsupported invoice fields: {', '.join(sorted(unexpected))}"
            )

        required_fields = {
            "customer_id",
            "issue_date",
            "due_date",
            "items",
        }

        missing = required_fields - set(data)

        if missing:
            raise InvoiceValidationError(
                f"Missing fields: {', '.join(sorted(missing))}"
            )

        customer = _validate_customer(
            tenant_id,
            data["customer_id"],
        )

        issue_date = _parse_date(
            data["issue_date"],
            "Issue date",
        )

        due_date = _parse_date(
            data["due_date"],
            "Due date",
        )

        if due_date < issue_date:
            raise InvoiceValidationError(
                "Due date cannot be earlier than issue date"
            )

        notes = _validate_notes(
            data.get("notes")
        )

        normalized_items = _normalize_items(
            data["items"]
        )

        tax_rate = _parse_decimal(
            data.get("tax_rate", ZERO),
            "Tax rate",
            2,
        )

        totals = _calculate_totals(
            normalized_items,
            tax_rate,
        )

        invoice_number = _allocate_invoice_number(
            tenant_id
        )

        invoice = Invoice(
            tenant_id=tenant_id,
            customer_id=customer.id,
            invoice_number=invoice_number,
            status="draft",
            currency="USD",
            issue_date=issue_date,
            due_date=due_date,
            subtotal=totals["subtotal"],
            tax_amount=totals["tax_amount"],
            total=totals["total"],
            notes=notes,
            tax_rate=tax_rate,
        )

        invoice.items = [
            InvoiceItem(**item)
            for item in normalized_items
        ]

        db.session.add(invoice)
        db.session.flush()

        invoice_id = invoice.id
        _commit_invoice_changes()

        return get_invoice(
            tenant_id,
            invoice_id,
        )

    except Exception:
        db.session.rollback()
        raise


def _get_invoice_for_update(tenant_id, invoice_id):
    """Retrieve a tenant-scoped invoice while locking its row where supported."""

    invoice = db.session.execute(
        select(Invoice)
        .where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if invoice is None:
        raise InvoiceNotFoundError("Invoice not found")

    # Load items separately after acquiring the invoice row lock.
    invoice.items

    return invoice


def get_invoice(tenant_id, invoice_id):
    """Retrieve an invoice and its items within a tenant."""

    invoice = db.session.execute(
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    if invoice is None:
        raise InvoiceNotFoundError("Invoice not found")

    return invoice


def list_invoices(
    tenant_id,
    page=1,
    per_page=20,
    status=None,
):
    """Return tenant invoices with pagination."""

    def parse_pagination_value(value, field):
        if isinstance(value, bool):
            raise InvoiceValidationError(
                f"{field} must be a positive integer"
            )

        if isinstance(value, int):
            parsed = value

        elif isinstance(value, str) and value.isdigit():
            parsed = int(value)

        else:
            raise InvoiceValidationError(
                f"{field} must be a positive integer"
            )

        if parsed < 1:
            raise InvoiceValidationError(
                f"{field} must be at least 1"
            )

        return parsed

    page = parse_pagination_value(page, "Page")
    per_page = parse_pagination_value(per_page, "per_page")

    if per_page > 100:
        raise InvoiceValidationError(
            "per_page cannot exceed 100"
        )

    query = select(Invoice).where(
        Invoice.tenant_id == tenant_id
    )

    count_query = select(
        func.count(Invoice.id)
    ).where(
        Invoice.tenant_id == tenant_id
    )

    if status is not None:

        if not isinstance(status, str):
            raise InvoiceValidationError(
                "Invalid invoice status filter"
            )

        if status not in ALLOWED_STATUSES:
            raise InvoiceValidationError(
                "Invalid invoice status filter"
            )

        query = query.where(
            Invoice.status == status
        )

        count_query = count_query.where(
            Invoice.status == status
        )

    total = db.session.execute(
        count_query
    ).scalar_one()

    invoices = db.session.execute(
        query
        .options(selectinload(Invoice.items))
        .order_by(
            Invoice.created_at.desc(),
            Invoice.id.desc(),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).scalars().all()

    return {
        "invoices": invoices,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


def update_invoice(tenant_id, invoice_id, data):
    """Update a draft invoice only, with row-level locking where supported."""

    if not isinstance(data, dict) or not data:
        raise InvoiceValidationError(
            "At least one field must be provided"
        )

    unexpected = set(data) - ALLOWED_INVOICE_FIELDS

    if unexpected:
        raise InvoiceValidationError(
            f"Unsupported invoice fields: {', '.join(sorted(unexpected))}"
        )

    try:
        invoice = _get_invoice_for_update(
            tenant_id,
            invoice_id,
        )

        if invoice.status != "draft":
            raise InvoiceStateError(
                "Only draft invoices can be modified"
            )

        if "customer_id" in data:
            customer = _validate_customer(
                tenant_id,
                data["customer_id"],
            )
            invoice.customer_id = customer.id

        if "issue_date" in data:
            invoice.issue_date = _parse_date(
                data["issue_date"],
                "Issue date",
            )

        if "due_date" in data:
            invoice.due_date = _parse_date(
                data["due_date"],
                "Due date",
            )

        if invoice.due_date < invoice.issue_date:
            raise InvoiceValidationError(
                "Due date cannot be earlier than issue date"
            )

        if "notes" in data:
            invoice.notes = _validate_notes(data["notes"])

        normalized_items = (
            _normalize_items(data["items"])
            if "items" in data
            else None
        )

        if normalized_items is not None or "tax_rate" in data:
            items_for_calculation = (
                normalized_items
                if normalized_items is not None
                else [
                    {"line_total": item.line_total}
                    for item in invoice.items
                ]
            )

            effective_tax_rate = (
                data["tax_rate"]
                if "tax_rate" in data
                else invoice.tax_rate
            )

            effective_tax_rate = _parse_decimal(
                effective_tax_rate,
                "Tax rate",
                2,
            )

            totals = _calculate_totals(
                items_for_calculation,
                effective_tax_rate,
            )

            if normalized_items is not None:
                invoice.items.clear()
                invoice.items.extend([
                    InvoiceItem(**item)
                    for item in normalized_items
                ])

            invoice.subtotal = totals["subtotal"]
            invoice.tax_rate = effective_tax_rate
            invoice.tax_amount = totals["tax_amount"]
            invoice.total = totals["total"]

        db.session.flush()
        _commit_invoice_changes()

        return get_invoice(tenant_id, invoice_id)

    except Exception:
        db.session.rollback()
        raise


def delete_invoice(tenant_id, invoice_id):
    """Delete a draft invoice only, with row-level locking where supported."""

    try:
        invoice = _get_invoice_for_update(
            tenant_id,
            invoice_id,
        )

        if invoice.status != "draft":
            raise InvoiceStateError(
                "Only draft invoices can be deleted"
            )

        db.session.delete(invoice)
        _commit_invoice_changes()

    except Exception:
        db.session.rollback()
        raise


def issue_invoice(tenant_id, invoice_id):
    """Transition a draft invoice to issued with row-level locking."""

    try:
        invoice = _get_invoice_for_update(
            tenant_id,
            invoice_id,
        )

        if invoice.status != "draft":
            raise InvoiceStateError(
                "Only draft invoices can be issued"
            )

        if not invoice.items:
            raise InvoiceValidationError(
                "An invoice must contain at least one item"
            )

        invoice.status = "issued"

        db.session.flush()
        _commit_invoice_changes()

        return get_invoice(
            tenant_id,
            invoice_id,
        )

    except Exception:
        db.session.rollback()
        raise
