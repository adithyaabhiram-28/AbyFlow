#!/usr/bin/env python3
"""Apply the project's Alembic migrations to the configured database."""
import os
import sys

# Ensure the project root is importable when invoked as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from flask_migrate import upgrade


def init_db():
    app = create_app()
    with app.app_context():
        upgrade(directory=os.path.join(os.path.dirname(__file__), '..', 'migrations'))
        print("Database migrations applied successfully.")


if __name__ == '__main__':
    init_db()
