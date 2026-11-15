
import re

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Customer


class CustomerAlreadyExistsError(Exception):
    """Raised when a customer email already exists in a tenant."""


class CustomerNotFoundError(Exception):
    """Raised when a customer does not belong to the requested tenant."""


class CustomerValidationError(Exception):
    """Raised when customer data is invalid."""


ALLOWED_FIELDS = {
    'name',
    'email',
    'company_name',
    'phone',
}

EMAIL_PATTERN = re.compile(
    r'^[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@'
    r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$'
)


def normalize_customer_data(data, partial=False):
    """Validate and normalize customer input."""
    if not isinstance(data, dict):
        raise CustomerValidationError(
            'Invalid JSON request body'
        )

    unexpected = set(data) - ALLOWED_FIELDS

    if unexpected:
        raise CustomerValidationError(
            f'Unsupported fields: {", ".join(sorted(unexpected))}'
        )

    if partial and not data:
        raise CustomerValidationError(
            'At least one field must be provided'
        )

    normalized = {}

    for field, value in data.items():

        if value is not None and not isinstance(value, str):
            raise CustomerValidationError(
                f'{field} must be a string'
            )

        if isinstance(value, str):
            value = value.strip()

        if field == 'name':
            if not value:
                raise CustomerValidationError(
                    'Customer name is required'
                )

            if len(value) > 150:
                raise CustomerValidationError(
                    'Customer name cannot exceed 150 characters'
                )

        elif field == 'email':
            value = value.lower() if value else None

            if value and (
                len(value) > 255
                or not EMAIL_PATTERN.fullmatch(value)
            ):
                raise CustomerValidationError(
                    'Invalid customer email'
                )

        elif field == 'company_name':
            if value and len(value) > 150:
                raise CustomerValidationError(
                    'Company name cannot exceed 150 characters'
                )

        elif field == 'phone':
            if value and len(value) > 30:
                raise CustomerValidationError(
                    'Phone number cannot exceed 30 characters'
                )

        normalized[field] = value

    if not partial and not normalized.get('name'):
        raise CustomerValidationError(
            'Customer name is required'
        )

    return normalized


def list_customers(tenant_id):
    """Return customers belonging only to the specified tenant."""
    return (
        Customer.query
        .filter_by(tenant_id=tenant_id)
        .order_by(Customer.created_at.desc(), Customer.id.desc())
        .all()
    )


def get_customer(tenant_id, customer_id):
    """Retrieve a customer within a tenant boundary."""
    customer = Customer.query.filter_by(
        id=customer_id,
        tenant_id=tenant_id,
    ).first()

    if not customer:
        raise CustomerNotFoundError(
            'Customer not found'
        )

    return customer


def _commit_customer_changes():
    """Commit changes and distinguish duplicate-email conflicts."""
    try:
        db.session.commit()

    except IntegrityError as error:
        db.session.rollback()

        # SQLite and PostgreSQL both identify unique constraint
        # violations in their underlying database error messages.
        original_error = str(error.orig).lower()

        if (
            'uq_customer_tenant_email' in original_error
            or 'customer.tenant_id, customer.email' in original_error
            or 'unique constraint failed: customer.tenant_id, customer.email'
            in original_error
        ):
            raise CustomerAlreadyExistsError(
                'A customer with this email already exists'
            ) from error

        # Do not disguise unrelated database errors.
        raise


def create_customer(tenant_id, data):
    """Create a customer inside a specific tenant."""
    normalized = normalize_customer_data(data)

    email = normalized.get('email')

    if email:
        existing = Customer.query.filter_by(
            tenant_id=tenant_id,
            email=email,
        ).first()

        if existing:
            raise CustomerAlreadyExistsError(
                'A customer with this email already exists'
            )

    customer = Customer(
        tenant_id=tenant_id,
        **normalized,
    )

    db.session.add(customer)
    _commit_customer_changes()

    return customer


def update_customer(tenant_id, customer_id, data):
    """Update a customer without crossing tenant boundaries."""
    customer = get_customer(tenant_id, customer_id)

    normalized = normalize_customer_data(
        data,
        partial=True,
    )

    if 'email' in normalized and normalized['email']:
        existing = Customer.query.filter(
            Customer.tenant_id == tenant_id,
            Customer.email == normalized['email'],
            Customer.id != customer_id,
        ).first()

        if existing:
            raise CustomerAlreadyExistsError(
                'A customer with this email already exists'
            )

    for field, value in normalized.items():
        setattr(customer, field, value)

    _commit_customer_changes()

    return customer


def delete_customer(tenant_id, customer_id):
    """Delete a customer belonging to the specified tenant."""
    customer = get_customer(tenant_id, customer_id)

    db.session.delete(customer)
    db.session.commit()
