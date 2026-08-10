#!/usr/bin/env python3
"""Generate a single-use emergency login code for AI Chief.

Usage (from the backend directory):
    python generate_emergency_code.py

Optional: EMERGENCY_CODE_TTL_HOURS=48 python generate_emergency_code.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.emergency_codes import generate_emergency_code  # noqa: E402


def main() -> None:
    ttl = int(os.environ.get("EMERGENCY_CODE_TTL_HOURS", "24"))
    code = generate_emergency_code(ttl_hours=ttl)
    print("=" * 60)
    print("AI Chief emergency login code")
    print("=" * 60)
    print()
    print(f"  {code}")
    print()
    print(f"Valid for {ttl} hour(s). Single-use. Enter it on the login screen")
    print('under "Use emergency code" if you cannot use your password.')
    print("=" * 60)


if __name__ == "__main__":
    main()
