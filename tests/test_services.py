from unittest.mock import patch, MagicMock
from app.models import User
from app.services.user_service import upgrade_user_to_pro, downgrade_user_to_free
from app.tasks import process_payment_task


def test_upgrade_user_to_pro_success(app, db, registered_user):
    """Test upgrading user from free to pro."""
    with app.app_context():
        success, msg = upgrade_user_to_pro(registered_user.email)
        assert success is True
        assert msg == "User upgraded to pro"

        user = User.query.filter_by(email=registered_user.email).first()
        assert user.plan_tier == 'pro'


def test_upgrade_user_already_pro(app, db, registered_user):
    """Test upgrading user who is already pro is idempotent."""
    with app.app_context():
        # First upgrade
        upgrade_user_to_pro(registered_user.email)
        # Second upgrade
        success, msg = upgrade_user_to_pro(registered_user.email)
        assert success is True
        assert msg == "User already pro"


def test_upgrade_user_not_found(app, db):
    """Test upgrading nonexistent user fails safely."""
    with app.app_context():
        success, msg = upgrade_user_to_pro("nonexistent@example.com")
        assert success is False
        assert msg == "User not found"


def test_downgrade_user_to_free(app, db, registered_user):
    """Test downgrading pro user to free."""
    with app.app_context():
        user = User.query.filter_by(email=registered_user.email).first()
        user.plan_tier = 'pro'
        db.session.commit()

        success, msg = downgrade_user_to_free(registered_user.email)
        assert success is True
        assert "downgraded" in msg

        user = User.query.filter_by(email=registered_user.email).first()
        assert user.plan_tier == 'free'


def test_downgrade_user_already_free(app, db, registered_user):
    """Test downgrading user who is already free is idempotent."""
    with app.app_context():
        success, msg = downgrade_user_to_free(registered_user.email)
        assert success is True
        assert "already free" in msg.lower()


def test_celery_task_execution(app, db, registered_user):
    """Test running process_payment_task directly against test database."""
    with patch('app.tasks.get_flask_app', return_value=app):
        result = process_payment_task(registered_user.email)
        assert result['status'] == 'success'
        assert result['message'] == 'User upgraded to pro'

        with app.app_context():
            user = User.query.filter_by(email=registered_user.email).first()
            assert user.plan_tier == 'pro'
