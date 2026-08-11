"""
Step 8: Test Gmail API access.
Just prints latest emails — no AI, no DB, no dashboard.

Usage:
  1. Download credentials.json from Google Cloud Console
     (APIs & Services > Credentials > OAuth 2.0 Client IDs > Desktop app)
  2. Place it at: backend/credentials/credentials.json
  3. Run: python test_gmail.py
"""

import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = os.path.join(
    os.path.dirname(__file__), "backend", "credentials", "credentials.json"
)
TOKEN_FILE = os.path.join(
    os.path.dirname(__file__), "backend", "credentials", "token.pickle"
)


def _load():
    from app.core.config import settings
    from app.core.security import decrypt_bytes

    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "rb") as f:
        return pickle.loads(decrypt_bytes(f.read(), settings.SECRET_KEY))


def _save(creds):
    from app.core.config import settings
    from app.core.security import encrypt_bytes

    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "wb") as f:
        f.write(encrypt_bytes(pickle.dumps(creds), settings.SECRET_KEY))


def get_gmail_service():
    creds = _load()

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"ERROR: credentials.json not found at {CREDENTIALS_FILE}")
                print("Download it from Google Cloud Console and place it there.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        _save(creds)

    return build("gmail", "v1", credentials=creds)


def main():
    service = get_gmail_service()
    if not service:
        return

    results = service.users().messages().list(userId="me", maxResults=10).execute()
    messages = results.get("messages", [])

    if not messages:
        print("No emails found.")
        return

    print("Latest Emails")
    print("-------------")

    for i, msg in enumerate(messages, 1):
        meta = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()

        headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "Unknown")
        subject = headers.get("Subject", "(No Subject)")

        # Shorten sender name
        short_sender = sender.split("<")[0].strip().strip("\"")
        if len(short_sender) > 30:
            short_sender = short_sender[:28] + "..."

        print(f"\n{i}. {short_sender}")
        print(f"   Subject: {subject}")


if __name__ == "__main__":
    main()
