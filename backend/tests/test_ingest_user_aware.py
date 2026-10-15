"""P1-4: email ingest is user-aware.

The ingest endpoint still requires the shared INGEST_API_KEY, but a claimed
user_id is only trusted when that user is active AND has an active Google
integration, and a claimed account_id must belong to that integration. With no
identity claimed the batch keeps the legacy single-user (owner) fallback.
"""

import datetime

from app.core.config import settings
from tests.conftest import auth  # noqa: F401

INGEST_URL = "/api/integrations/emails/ingest"


def _headers():
    return {"X-API-Key": settings.INGEST_API_KEY}


def _batch(user_id=None, account_id=None, message_id="m-1"):
    batch = {
        "emails": [
            {
                "message_id": message_id,
                "thread_id": f"t-{message_id}",
                "from_email": "alice@example.com",
                "from_name": "Alice",
                "subject": "Project update",
                "body_text": "hello there",
                "received_at": "2026-01-01T10:00:00",
                "is_read": False,
            }
        ]
    }
    if user_id is not None:
        batch["user_id"] = user_id
    if account_id is not None:
        batch["account_id"] = account_id
    return batch


def _seed_google_integration(db, user_id, account_id="member@google.test", status="active"):
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=user_id,
            provider="google",
            status=status,
            account_id=account_id,
        )
    )
    db.commit()


def test_ingest_requires_api_key(client, owner):
    r = client.post(INGEST_URL, json=_batch())
    assert r.status_code == 401
    assert client.post(INGEST_URL, json=_batch(), headers={"X-API-Key": "wrong"}).status_code == 401


def test_ingest_without_user_attributed_to_owner(client, db, owner, member):
    r = client.post(INGEST_URL, json=_batch(message_id="m-owner-1"), headers=_headers())
    assert r.status_code == 200
    assert r.json()["inserted"] == 1

    from app.models.email import Email

    row = db.query(Email).filter(Email.message_id == "m-owner-1").first()
    assert row is not None
    assert row.user_id == owner["id"]
    assert row.subject == "Project update"


def test_ingest_with_valid_user_and_account(client, db, owner, member):
    from app.models.email import Email

    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(user_id=member["id"], account_id="member@google.test", message_id="m-member-1"),
        headers=_headers(),
    )
    assert r.status_code == 200
    assert r.json()["inserted"] == 1

    row = db.query(Email).filter(Email.message_id == "m-member-1").first()
    assert row.user_id == member["id"]


def test_ingest_rejects_account_mismatch(client, db, owner, member):
    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(user_id=member["id"], account_id="someone-else@google.test"),
        headers=_headers(),
    )
    assert r.status_code == 403


def test_ingest_rejects_unknown_user(client, owner):
    r = client.post(INGEST_URL, json=_batch(user_id=999999), headers=_headers())
    assert r.status_code == 404


def test_ingest_requires_active_google_integration(client, db, owner, member):
    # member exists but has no integration at all
    r = client.post(INGEST_URL, json=_batch(user_id=member["id"]), headers=_headers())
    assert r.status_code == 403

    # revoked integration is no better
    _seed_google_integration(db, member["id"], status="revoked")
    r = client.post(INGEST_URL, json=_batch(user_id=member["id"]), headers=_headers())
    assert r.status_code == 403


def test_ingest_is_idempotent_per_message(client, db, owner, member):
    from app.models.email import Email

    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r1 = client.post(
        INGEST_URL,
        json=_batch(user_id=member["id"], account_id="member@google.test", message_id="m-unique"),
        headers=_headers(),
    )
    assert r1.json()["inserted"] == 1

    r2 = client.post(
        INGEST_URL,
        json=_batch(user_id=member["id"], account_id="member@google.test", message_id="m-unique"),
        headers=_headers(),
    )
    assert r2.json()["inserted"] == 0
    assert r2.json()["ignored"] == 1
    assert db.query(Email).count() == 1


def test_ingest_rows_keep_identity_when_processed(client, db, owner, member, monkeypatch):
    """AI processing of an ingested email retains the user the batch belonged to."""
    from app.models.email import Email
    from app.services import ai_processor, email_processor

    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(user_id=member["id"], account_id="member@google.test", message_id="m-ai-1"),
        headers=_headers(),
    )
    assert r.status_code == 200
    email_id = r.json()["results"][0]["email_id"]

    async def _fake_analyze(subject="", body=""):
        return {"category": "meeting", "summary": "standup"}

    async def _fake_schedule(subject="", body=""):
        return {"items": []}

    monkeypatch.setattr("app.services.ai_service.ai_service.analyze_email", _fake_analyze)
    monkeypatch.setattr("app.services.ai_service.ai_service.extract_schedule", _fake_schedule)

    ai_processor._run_job(email_id)
    email_processor.process_email_sync(email_id)

    row = db.query(Email).filter(Email.message_id == "m-ai-1").first()
    assert row.user_id == member["id"]
    assert row.category == "meeting"