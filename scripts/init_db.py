#!/usr/bin/env python3
"""Database initialization script for AbyFlow."""
import sys
import os

# Add root directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.models import User, ProcessedEvent


def init_db():
    app = create_app()
    with app.app_context():
        print("Ensuring database tables exist...")
        db.create_all()
        print("Database initialized successfully.")


if __name__ == '__main__':
    init_db()
