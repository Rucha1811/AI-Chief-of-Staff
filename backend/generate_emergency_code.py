#!/usr/bin/env python3
"""Generate a single-use emergency login code for AI Chief.

Emergency codes are bound to exactly one user: the person logging in must
present BOTH the account email and the code. By default the code is bound to
the owner (earliest) user; use EMERGENCY_CODE_EMAIL to bind it to a specific
account instead.

Usage (from the backend directory):
    python generate_emergency_code.py
    EMERGENCY_CODE_EMAIL=member@example.com python generate_emergency_code.py

Optional: EMERGENCY_CODE_TTL_HOURS=48 python generate_emergency_code.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.emergency_codes import generate_emergency_code  # noqa: E402


def _resolve_user(db) -> User:
    email = os.environ.get("EMERGENCY_CODE_EMAIL")
    if email:
        user = (
            db.query(User)
            .filter(User.email == email.strip().lower(), User.status == "active")
            .order_by(User.id.asc())
            .first()
        )
        if user is None:
            sys.exit(f"ERROR: no active user with email '{email}' found.")
        return user
    user = db.query(User).filter(User.status == "active").order_by(User.id.asc()).first()
    if user is None:
        sys.exit("ERROR: no users exist yet; nothing to bind the code to.")
    return user


def main() -> None:
    db = SessionLocal()
    try:
        user = _resolve_user(db)
        ttl = int(os.environ.get("EMERGENCY_CODE_TTL_HOURS", "24"))
        code = generate_emergency_code(user_id=user.id, ttl_hours=ttl, db=db)
    finally:
        db.close()
    print("=" * 60)
    print("AI Chief emergency login code")
    print("=" * 60)
    print()
    print(f"  {code}")
    print()
    print(f"Valid for {ttl} hour(s). Single-use.")
    print(f"Bound to account: {user.email}")
    print("Enter it (with your account email) on the login screen under")
    print('"Use emergency code" if you cannot use your password.')
    print("=" * 60)


if __name__ == "__main__":
    main()