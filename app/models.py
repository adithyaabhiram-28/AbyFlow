from sqlalchemy import and_
from sqlalchemy.orm import foreign
from app.extentions import db


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    plan_tier = db.Column(db.String(50), default='free')

    # A user can belong to multiple tenants.
    tenant_memberships = db.relationship(
        'TenantMembership',
        back_populates='user',
        cascade='all, delete-orphan'
    )


class ProcessedEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stripe_event_id = db.Column(db.String(100), unique=True, nullable=False)


class Tenant(db.Model):
    __tablename__ = 'tenant'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(150), unique=True, nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now()
    )

    memberships = db.relationship(
        'TenantMembership',
        back_populates='tenant',
        cascade='all, delete-orphan'
    )

    customers = db.relationship(
        'Customer',
        back_populates='tenant',
        cascade='all, delete-orphan'
    )
    invoices = db.relationship(
        'Invoice',
        back_populates='tenant',
        cascade='all, delete-orphan'
    )


class TenantMembership(db.Model):
    __tablename__ = 'tenant_membership'

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'tenant_id',
            name='uq_tenant_membership_user_tenant'
        ),
        db.CheckConstraint(
            "role IN ('owner', 'admin', 'member')",
            name='ck_tenant_membership_valid_role'
        ),
        db.Index('ix_tenant_membership_tenant_id', 'tenant_id'),
    )

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )

    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=False
    )

    role = db.Column(db.String(20), nullable=False, default='member')

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    user = db.relationship(
        'User',
        back_populates='tenant_memberships'
    )

    tenant = db.relationship(
        'Tenant',
        back_populates='memberships'
    )


class Customer(db.Model):
    __tablename__ = 'customer'

    __table_args__ = (
        db.UniqueConstraint(
            'tenant_id',
            'email',
            name='uq_customer_tenant_email'
        ),
        db.Index(
            'ix_customer_tenant_id_created_at',
            'tenant_id',
            'created_at'
        ),
        db.UniqueConstraint(
            'tenant_id',
            'id',
            name='uq_customer_tenant_id_id'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=False
    )

    name = db.Column(
        db.String(150),
        nullable=False
    )

    email = db.Column(
        db.String(255),
        nullable=True
    )

    company_name = db.Column(
        db.String(150),
        nullable=True
    )

    phone = db.Column(
        db.String(30),
        nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now()
    )

    tenant = db.relationship(
        'Tenant',
        back_populates='customers'
    )

    invoices = db.relationship(
        'Invoice',
        primaryjoin=lambda: and_(
            Customer.id == foreign(Invoice.customer_id),
            Customer.tenant_id == Invoice.tenant_id,
        ),
        back_populates='customer'
    )


class Invoice(db.Model):
    __tablename__ = 'invoice'

    __table_args__ = (
        db.UniqueConstraint(
            'tenant_id',
            'invoice_number',
            name='uq_invoice_tenant_number'
        ),
        db.ForeignKeyConstraint(
            ['tenant_id', 'customer_id'],
            ['customer.tenant_id', 'customer.id'],
            name='fk_invoice_tenant_customer',
            ondelete='RESTRICT'
        ),
        db.CheckConstraint(
            "status IN ('draft', 'issued', 'paid', 'overdue', 'cancelled')",
            name='ck_invoice_valid_status'
        ),
        db.CheckConstraint(
            'tax_rate >= 0 AND tax_rate <= 100',
            name='ck_invoice_valid_tax_rate'
        ),
        db.CheckConstraint(
            "currency = 'USD'",
            name='ck_invoice_usd_only'
        ),
        db.CheckConstraint(
            'subtotal >= 0 AND tax_amount >= 0 AND total >= 0',
            name='ck_invoice_nonnegative_amounts'
        ),
        db.CheckConstraint(
            'due_date >= issue_date',
            name='ck_invoice_due_date'
        ),
        db.Index(
            'ix_invoice_tenant_status',
            'tenant_id',
            'status'
        ),
        db.Index(
            'ix_invoice_tenant_created_at',
            'tenant_id',
            'created_at'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=False
    )

    customer_id = db.Column(
        db.Integer,
        nullable=False
    )

    invoice_number = db.Column(
        db.String(50),
        nullable=False
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default='draft',
        server_default='draft'
    )

    currency = db.Column(
        db.String(3),
        nullable=False,
        default='USD',
        server_default='USD'
    )

    issue_date = db.Column(
        db.Date,
        nullable=False
    )

    due_date = db.Column(
        db.Date,
        nullable=False
    )

    subtotal = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    tax_rate = db.Column(
        db.Numeric(5, 2),
        nullable=False,
        default=0,
        server_default='0'
    )

    tax_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    total = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    notes = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now()
    )

    tenant = db.relationship(
        'Tenant',
        back_populates='invoices'
    )

    customer = db.relationship(
        'Customer',
        primaryjoin=lambda: and_(
            Customer.id == foreign(Invoice.customer_id),
            Customer.tenant_id == Invoice.tenant_id,
        ),
        back_populates='invoices'
    )

    items = db.relationship(
        'InvoiceItem',
        back_populates='invoice',
        cascade='all, delete-orphan',
        order_by='InvoiceItem.id'
    )


class InvoiceItem(db.Model):
    __tablename__ = 'invoice_item'

    __table_args__ = (
        db.CheckConstraint(
            'quantity > 0',
            name='ck_invoice_item_positive_quantity'
        ),
        db.CheckConstraint(
            'unit_price >= 0 AND line_total >= 0',
            name='ck_invoice_item_nonnegative_amounts'
        ),
        db.Index(
            'ix_invoice_item_invoice_id',
            'invoice_id'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey('invoice.id', ondelete='CASCADE'),
        nullable=False
    )

    description = db.Column(
        db.String(255),
        nullable=False
    )

    quantity = db.Column(
        db.Numeric(12, 3),
        nullable=False
    )

    unit_price = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    line_total = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    invoice = db.relationship(
        'Invoice',
        back_populates='items'
    )

class InvoiceSequence(db.Model):
    __tablename__ = 'invoice_sequence'

    __table_args__ = (
        db.UniqueConstraint(
            'tenant_id',
            name='uq_invoice_sequence_tenant'
        ),
        db.CheckConstraint(
            'next_number >= 1',
            name='ck_invoice_sequence_positive_number'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=False
    )

    next_number = db.Column(
        db.Integer,
        nullable=False,
        default=1,
        server_default='1'
    )
