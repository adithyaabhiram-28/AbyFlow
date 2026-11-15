#!/usr/bin/env python
"""
Controlled administrative script to provision platform operator/admin privileges.
This script is run from a privileged host terminal or container environment.
It cannot be invoked from public HTTP endpoints.

Usage:
    python scripts/promote_operator.py user@example.com --role admin
    python scripts/promote_operator.py user@example.com --role operator
"""

import sys
import argparse
from app import create_app
from app.extensions import db
from app.models import User
from app.services.platform_authorization import PLATFORM_ROLES


def promote_user(email: str, role: str):
    if role not in PLATFORM_ROLES:
        print(f"Error: Invalid role '{role}'. Permitted roles: {sorted(PLATFORM_ROLES)}")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        user = User.query.filter_by(email=email.strip().lower()).first()
        if not user:
            print(f"Error: User with email '{email}' not found.")
            sys.exit(1)

        old_role = user.platform_role
        user.platform_role = role
        db.session.commit()
        print(f"Success: User '{user.email}' platform_role changed from '{old_role}' to '{role}'.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Assign platform role to a user")
    parser.add_argument("email", help="User email address to promote")
    parser.add_argument(
        "--role",
        choices=["user", "operator", "admin"],
        default="admin",
        help="Target platform role (default: admin)"
    )

    args = parser.parse_args()
    promote_user(args.email, args.role)

