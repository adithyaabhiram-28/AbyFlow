from app.extentions import db

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    plan_tier = db.Column(db.String(50), default='free')

class ProcessedEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stripe_event_id = db.Column(db.String(100), unique=True, nullable=False)
