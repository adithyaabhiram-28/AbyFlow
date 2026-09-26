# Backward-compatibility alias for legacy imports
from app.extensions import db, bcrypt, jwt

__all__ = ['db', 'bcrypt', 'jwt']