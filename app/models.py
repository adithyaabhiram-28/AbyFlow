from sqlalchemy import and_, Float
from sqlalchemy.orm import foreign
from sqlalchemy.types import TypeDecorator, JSON
from app.extentions import db

class EmbeddingVectorComparator(TypeDecorator.Comparator):
    def cosine_distance(self, other):
        return self.expr.op("<=>", return_type=Float)(other)

class EmbeddingVector(TypeDecorator):
    """
    Vector type decorator:
    - PostgreSQL: native pgvector Vector(1536).
    - SQLite: JSON for test compatibility.
    """
    impl = JSON
    cache_ok = True
    comparator_factory = EmbeddingVectorComparator

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            try:
                from pgvector.sqlalchemy import Vector
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL requires the pgvector Python package. "
                    "Install dependencies from requirements.txt."
                ) from exc

            return dialect.type_descriptor(Vector(1536))

        return dialect.type_descriptor(JSON())



class User(db.Model):
    __table_args__ = (
        db.CheckConstraint(
            "platform_role IN ('user', 'operator', 'admin')",
            name='ck_user_valid_platform_role'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    plan_tier = db.Column(db.String(50), default='free')

    # Platform-level permissions, independent of tenant membership.
    platform_role = db.Column(
        db.String(20),
        nullable=False,
        default='user',
        server_default='user'
    )

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


class BillingEvent(db.Model):
    __tablename__ = 'billing_event'

    __table_args__ = (
        db.UniqueConstraint(
            'stripe_event_id',
            name='uq_billing_event_stripe_event_id'
        ),
        db.Index(
            'ix_billing_event_type_created',
            'event_type',
            'created_at'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    stripe_event_id = db.Column(
        db.String(100),
        nullable=False
    )

    event_type = db.Column(
        db.String(100),
        nullable=False
    )

    payload = db.Column(
        db.JSON,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )


class OutboxEvent(db.Model):
    __tablename__ = 'outbox_event'

    __table_args__ = (
        db.UniqueConstraint(
            'billing_event_id',
            name='uq_outbox_event_billing_event_id'
        ),
        db.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name='ck_outbox_event_valid_status'
        ),
        db.CheckConstraint(
            'attempts >= 0',
            name='ck_outbox_event_nonnegative_attempts'
        ),
        db.Index(
            'ix_outbox_event_status_created',
            'status',
            'created_at'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    billing_event_id = db.Column(
        db.Integer,
        db.ForeignKey('billing_event.id', ondelete='CASCADE'),
        nullable=False
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default='pending',
        server_default='pending'
    )

    attempts = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default='0'
    )

    available_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    locked_until = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    lease_token = db.Column(
        db.String(36),
        nullable=True
    )

    last_error = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )

    completed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    billing_event = db.relationship(
        'BillingEvent',
        backref=db.backref('outbox_event', uselist=False)
    )


class Payment(db.Model):
    __tablename__ = 'payment'

    __table_args__ = (
        db.UniqueConstraint(
            'stripe_checkout_session_id',
            name='uq_payment_stripe_checkout_session'
        ),
        db.UniqueConstraint(
            'stripe_payment_intent_id',
            name='uq_payment_stripe_payment_intent'
        ),
        db.CheckConstraint(
            'amount_cents >= 0',
            name='ck_payment_nonnegative_amount'
        ),
        db.CheckConstraint(
            "currency = 'USD'",
            name='ck_payment_usd_only'
        ),
        db.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name='ck_payment_valid_status'
        ),
        db.Index('ix_payment_user_created_at', 'user_id', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='RESTRICT'),
        nullable=False
    )

    stripe_checkout_session_id = db.Column(
        db.String(255),
        nullable=False
    )

    stripe_payment_intent_id = db.Column(
        db.String(255),
        nullable=True
    )

    amount_cents = db.Column(db.Integer, nullable=False)

    currency = db.Column(
        db.String(3),
        nullable=False,
        default='USD',
        server_default='USD'
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default='pending',
        server_default='pending'
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

    user = db.relationship('User', backref='payments')

class OutboxReplayAudit(db.Model):
    __tablename__ = 'outbox_replay_audit'

    __table_args__ = (
        db.CheckConstraint(
            'previous_attempts >= 0',
            name='ck_outbox_replay_audit_nonnegative_attempts'
        ),
        db.Index(
            'ix_outbox_replay_audit_event_replayed',
            'outbox_event_id',
            'replayed_at'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    outbox_event_id = db.Column(
        db.Integer,
        db.ForeignKey('outbox_event.id', ondelete='RESTRICT'),
        nullable=False
    )

    stripe_event_id = db.Column(
        db.String(100),
        nullable=False
    )

    operator_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='RESTRICT'),
        nullable=False
    )

    reason = db.Column(
        db.String(500),
        nullable=False
    )

    previous_attempts = db.Column(
        db.Integer,
        nullable=False
    )

    replayed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now()
    )


class AIConversation(db.Model):
    __tablename__ = 'ai_conversation'

    __table_args__ = (
        db.Index('ix_ai_conversation_tenant_user', 'tenant_id', 'user_id'),
        db.Index('ix_ai_conversation_created_at', 'created_at'),
    )

    id = db.Column(db.String(36), primary_key=True)  # UUID
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=False
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )
    title = db.Column(db.String(200), nullable=False, default='Billing Inquiry', server_default='Billing Inquiry')
    status = db.Column(db.String(20), nullable=False, default='active', server_default='active')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now(), onupdate=db.func.now())

    tenant = db.relationship('Tenant', backref=db.backref('ai_conversations', cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('ai_conversations', cascade='all, delete-orphan'))
    messages = db.relationship('AIMessage', back_populates='conversation', cascade='all, delete-orphan', order_by='AIMessage.created_at')
    usage_records = db.relationship('AIUsage', back_populates='conversation', cascade='all, delete-orphan')


class AIMessage(db.Model):
    __tablename__ = 'ai_message'

    __table_args__ = (
        db.Index('ix_ai_message_conversation_created', 'conversation_id', 'created_at'),
        db.CheckConstraint("role IN ('system', 'user', 'assistant', 'tool')", name='ck_ai_message_valid_role'),
    )

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.String(36),
        db.ForeignKey('ai_conversation.id', ondelete='CASCADE'),
        nullable=False
    )
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=True)
    tool_call_id = db.Column(db.String(100), nullable=True)
    tool_name = db.Column(db.String(100), nullable=True)
    tool_arguments = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    conversation = db.relationship('AIConversation', back_populates='messages')


class AIUsage(db.Model):
    __tablename__ = 'ai_usage'

    __table_args__ = (
        db.Index('ix_ai_usage_tenant_user', 'tenant_id', 'user_id'),
        db.Index('ix_ai_usage_created_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.String(36),
        db.ForeignKey('ai_conversation.id', ondelete='SET NULL'),
        nullable=True
    )
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=False
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )
    model = db.Column(db.String(50), nullable=False)
    prompt_tokens = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    completion_tokens = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    total_tokens = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    estimated_cost_usd = db.Column(db.Numeric(10, 6), nullable=False, default=0, server_default='0')
    latency_ms = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    conversation = db.relationship('AIConversation', back_populates='usage_records')
    tenant = db.relationship('Tenant', backref=db.backref('ai_usage_records', cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('ai_usage_records', cascade='all, delete-orphan'))


class KnowledgeDocument(db.Model):
    __tablename__ = 'knowledge_document'

    __table_args__ = (
        db.Index('ix_knowledge_document_tenant_slug', 'tenant_id', 'slug'),
        db.UniqueConstraint('tenant_id', 'slug', 'version', name='uq_knowledge_doc_tenant_slug_version'),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=True
    )
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='policy', server_default='policy')
    version = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='true')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now(), onupdate=db.func.now())

    tenant = db.relationship('Tenant', backref=db.backref('knowledge_documents', cascade='all, delete-orphan'))
    chunks = db.relationship('DocumentChunk', back_populates='document', cascade='all, delete-orphan')


class DocumentChunk(db.Model):
    __tablename__ = 'document_chunk'

    __table_args__ = (
        db.Index('ix_document_chunk_doc_tenant', 'document_id', 'tenant_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(
        db.Integer,
        db.ForeignKey('knowledge_document.id', ondelete='CASCADE'),
        nullable=False
    )
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenant.id', ondelete='CASCADE'),
        nullable=True
    )
    chunk_index = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(EmbeddingVector, nullable=False)
    token_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    document = db.relationship('KnowledgeDocument', back_populates='chunks')
    tenant = db.relationship('Tenant', backref=db.backref('document_chunks', cascade='all, delete-orphan'))
