import logging
from app.extensions import db
from app.models import User

logger = logging.getLogger(__name__)


def upgrade_user_to_pro(email: str):
    """
    Upgrade a user's subscription tier to 'pro'.
    Idempotent: returns success if already 'pro'.
    """
    if not email or not isinstance(email, str):
        return False, "Invalid email format"

    normalized_email = email.strip().lower()

    try:
        user = User.query.filter_by(email=normalized_email).first()
        if not user:
            logger.warning("Upgrade failed: User with email %s not found", normalized_email)
            return False, "User not found"

        if user.plan_tier == 'pro':
            logger.info("User %s already has plan_tier 'pro'", normalized_email)
            return True, "User already pro"

        user.plan_tier = 'pro'
        db.session.commit()
        logger.info("Successfully upgraded user %s to 'pro'", normalized_email)
        return True, "User upgraded to pro"

    except Exception as e:
        db.session.rollback()
        logger.error("Database error upgrading user %s: %s", normalized_email, str(e))
        return False, f"Database error: {str(e)}"


def downgrade_user_to_free(email: str):
    """
    Downgrade a user's subscription tier to 'free'.
    Idempotent: returns success if already 'free'.
    """
    if not email or not isinstance(email, str):
        return False, "Invalid email format"

    normalized_email = email.strip().lower()

    try:
        user = User.query.filter_by(email=normalized_email).first()
        if not user:
            logger.warning("Downgrade failed: User with email %s not found", normalized_email)
            return False, "User not found"

        if user.plan_tier == 'free':
            logger.info("User %s already has plan_tier 'free'", normalized_email)
            return True, "User already free"

        user.plan_tier = 'free'
        db.session.commit()
        logger.info("Successfully downgraded user %s to 'free'", normalized_email)
        return True, "User downgraded to free due to payment failure"

    except Exception as e:
        db.session.rollback()
        logger.error("Database error downgrading user %s: %s", normalized_email, str(e))
        return False, f"Database error: {str(e)}"