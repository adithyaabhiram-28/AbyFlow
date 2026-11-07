
from app.models import User, TenantMembership


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
