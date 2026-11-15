
import re
from app.extensions import db
from app.models import User, Tenant, TenantMembership

VALID_TENANT_ROLES = {'owner', 'admin', 'member'}


class TenantServiceError(Exception):
    """Base exception for tenant service operations."""


class TenantValidationError(TenantServiceError):
    """Raised when tenant input is invalid."""


class TenantNotFoundError(TenantServiceError):
    """Raised when tenant is not found."""


class TenantMemberNotFoundError(TenantServiceError):
    """Raised when tenant member is not found."""


class TenantConflictError(TenantServiceError):
    """Raised when tenant slug or member already exists."""


def slugify(text: str) -> str:
    """Generate a URL-safe slug from text."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return re.sub(r'^-+|-+$', '', text)


def get_user_by_email(email: str):
    """Retrieve a user using their normalized email address."""
    if not isinstance(email, str):
        return None

    return User.query.filter_by(
        email=email.strip().lower()
    ).first()


def get_user_tenant_memberships(user: User):
    """Return all tenant memberships belonging to a user."""
    return TenantMembership.query.filter_by(
        user_id=user.id
    ).all()


def get_tenant_membership(user: User, tenant_id: int):
    """Find a user's membership in a specific tenant."""
    return TenantMembership.query.filter_by(
        user_id=user.id,
        tenant_id=tenant_id
    ).first()


def has_required_role(
    membership: TenantMembership,
    allowed_roles: set[str],
) -> bool:
    """Check whether a membership has one of the required roles."""
    return (
        membership is not None
        and membership.role in allowed_roles
    )


def create_tenant(user: User, name: str, slug: str = None) -> Tenant:
    """Create a new tenant and assign the user as owner."""
    if not isinstance(name, str) or not name.strip():
        raise TenantValidationError("Tenant name is required")
    name = name.strip()
    if len(name) > 120:
        raise TenantValidationError("Tenant name cannot exceed 120 characters")

    if slug:
        if not isinstance(slug, str) or not slug.strip():
            raise TenantValidationError("Invalid tenant slug")
        slug = slugify(slug)
    else:
        slug = slugify(name)

    if not slug or len(slug) > 150:
        raise TenantValidationError("Invalid tenant slug")

    existing = Tenant.query.filter_by(slug=slug).first()
    if existing:
        raise TenantConflictError(f"A workspace with slug '{slug}' already exists")

    tenant = Tenant(name=name, slug=slug)
    db.session.add(tenant)
    db.session.flush()

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant.id,
        role='owner'
    )
    db.session.add(membership)
    db.session.commit()
    return tenant


def list_tenant_members(tenant_id: int):
    """List all members of a tenant."""
    memberships = (
        TenantMembership.query
        .filter_by(tenant_id=tenant_id)
        .join(User, TenantMembership.user_id == User.id)
        .order_by(TenantMembership.created_at.asc())
        .all()
    )
    return [
        {
            'user_id': m.user.id,
            'email': m.user.email,
            'role': m.role,
            'created_at': m.created_at.isoformat() if m.created_at else None
        }
        for m in memberships
    ]


def add_tenant_member(tenant_id: int, email: str, role: str = 'member'):
    """Add an existing user to a tenant."""
    if not isinstance(email, str) or not email.strip():
        raise TenantValidationError("Email is required")
    email = email.strip().lower()

    if role not in VALID_TENANT_ROLES:
        raise TenantValidationError(f"Invalid role. Must be one of: {', '.join(sorted(VALID_TENANT_ROLES))}")

    user = User.query.filter_by(email=email).first()
    if not user:
        raise TenantValidationError(f"User with email '{email}' does not exist")

    existing_membership = TenantMembership.query.filter_by(
        user_id=user.id,
        tenant_id=tenant_id
    ).first()
    if existing_membership:
        raise TenantConflictError(f"User '{email}' is already a member of this workspace")

    membership = TenantMembership(
        user_id=user.id,
        tenant_id=tenant_id,
        role=role
    )
    db.session.add(membership)
    db.session.commit()
    return {
        'user_id': user.id,
        'email': user.email,
        'role': membership.role,
        'created_at': membership.created_at.isoformat() if membership.created_at else None
    }


def update_tenant_member_role(tenant_id: int, target_user_id: int, new_role: str):
    """Update a tenant member's role."""
    if new_role not in VALID_TENANT_ROLES:
        raise TenantValidationError(f"Invalid role. Must be one of: {', '.join(sorted(VALID_TENANT_ROLES))}")

    membership = TenantMembership.query.filter_by(
        tenant_id=tenant_id,
        user_id=target_user_id
    ).first()
    if not membership:
        raise TenantMemberNotFoundError("Member not found in this workspace")

    if membership.role == 'owner' and new_role != 'owner':
        owners_count = TenantMembership.query.filter_by(
            tenant_id=tenant_id,
            role='owner'
        ).count()
        if owners_count <= 1:
            raise TenantValidationError("Cannot demote the last owner of the workspace")

    membership.role = new_role
    db.session.commit()
    return {
        'user_id': membership.user.id,
        'email': membership.user.email,
        'role': membership.role
    }


def remove_tenant_member(tenant_id: int, target_user_id: int):
    """Remove a member from a tenant."""
    membership = TenantMembership.query.filter_by(
        tenant_id=tenant_id,
        user_id=target_user_id
    ).first()
    if not membership:
        raise TenantMemberNotFoundError("Member not found in this workspace")

    if membership.role == 'owner':
        owners_count = TenantMembership.query.filter_by(
            tenant_id=tenant_id,
            role='owner'
        ).count()
        if owners_count <= 1:
            raise TenantValidationError("Cannot remove the last owner of the workspace")

    db.session.delete(membership)
    db.session.commit()
