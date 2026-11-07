
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
