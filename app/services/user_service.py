from app.extentions import db
from app.models import User

def upgrade_user_to_pro(email):
    print("=" * 50)
    print("LOOKING FOR EMAIL:", email)

    all_users = User.query.all()
    print("ALL USERS:", [u.email for u in all_users])

    user = User.query.filter_by(email=email).first()
    print("FOUND:", user)
    if not user:
        return False, "User not found"
    if user.plan_tier == 'pro':
        return True, "User already pro"
    
    user.plan_tier = 'pro'
    db.session.commit()
    return True, "User upgraded to pro"

def downgrade_user_to_free(email):
    user = User.query.filter_by(email=email).first()
    if not user:
        return False, "User not found"
    if user.plan_tier == 'free':
        return True, "User already Free"
    user.plan_tier = "free"
    db.session.commit()
    return True, "User downgraded to free due to payment failure"