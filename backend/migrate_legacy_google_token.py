#!/usr/bin/env python3
"""One-time migration: import the legacy global Google token.pickle into the
owner's per-user user_integrations row.

The normal runtime NEVER reads token.pickle anymore. This script is the only
place that reads it, and it is intentionally NOT part of the app startup path.

Usage:

    cd backend && python migrate_legacy_google_token.py
    # or inside Docker:
    docker compose exec backend python migrate_legacy_google_token.py

Behavior (backup the file first if unsure — the file is never deleted, it is
renamed to token.pickle.legacy only AFTER a successful import):

  1. Reads backend/credentials/token.pickle (an encrypted pickle under
     SECRET_KEY).
  2. If the owner already has an active google integration with encrypted
     credentials, it does NOT overwrite them (owner web-OAuth wins) and simply
     archives the legacy file.
  3. Otherwise it stores the migrated credentials in the owner's
     user_integrations row (encrypted at rest) and renames the file to
     token.pickle.legacy so no runtime or future run ever sees it again.
"""
import os
import pickle
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import decrypt_bytes  # noqa: E402
from app.services import google_auth  # noqa: E402
from app.services.ownership import owner_user_id  # noqa: E402

TOKEN_FILE = os.path.join(BACKEND, "credentials", "token.pickle")
_ARCHIVED = TOKEN_FILE + ".legacy"


def _archive() -> None:
    target = _ARCHIVED
    counter = 0
    while os.path.exists(target):
        counter += 1
        target = f"{_ARCHIVED}.{counter}"
    os.replace(TOKEN_FILE, target)
    print(f"Archived {TOKEN_FILE} -> {target}")


def main() -> int:
    if not os.path.exists(TOKEN_FILE):
        print("No token.pickle found; nothing to migrate.")
        return 0
    db = SessionLocal()
    try:
        owner_id = owner_user_id(db)
        row = google_auth.integration_row(db, provider="google", user_id=owner_id)
        if row is not None and row.refresh_token_encrypted:
            print(
                "Owner already has encrypted Google credentials; not overwriting. "
                "Archiving legacy file."
            )
            _archive()
            return 0
        try:
            with open(TOKEN_FILE, "rb") as f:
                raw = f.read()
            creds = pickle.loads(decrypt_bytes(raw, settings.SECRET_KEY))
        except Exception as e:
            print(f"Could not decrypt legacy token.pickle: {e}")
            print("Leaving the file untouched. Fix manually if needed.")
            return 1
        google_auth.save_credentials_for_user(
            db,
            owner_id,
            creds,
            metadata={"imported_from": "token.pickle", "auth_label": "Legacy Google import"},
        )
        print(f"Imported legacy Google credentials for owner #{owner_id}.")
        _archive()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())