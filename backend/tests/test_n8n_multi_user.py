"""P2.1: the agent endpoints + account-based identity make n8n multi-user.

FastAPI is authoritative for identity. n8n identifies work by the Gmail
``account_id`` only; the server resolves it against the trusted integration
mapping (user_integrations, populated by per-user OAuth) and never trusts a
raw numeric ``user_id`` from workflow input.
"""

from app.core.config import settings

INGEST_URL = "/api/integrations/emails/ingest"
AGENT_URL = "/api/integrations/agent"
SYNC_URL = "/api/integrations/agent/sync"


def _headers():
    return {"X-API-Key": settings.INGEST_API_KEY}


def _batch(user_id=None, account_id=None, message_id="m-a1"):
    batch = {
        "emails": [
            {
                "message_id": message_id,
                "thread_id": f"t-{message_id}",
                "from_email": "alice@example.com",
                "subject": "hello",
                "body_text": "body",
                "received_at": "2026-01-01T10:00:00",
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

    row = (
        db.query(UserIntegration)
        .filter(UserIntegration.user_id == user_id, UserIntegration.provider == "google")
        .first()
    )
    if row is None:
        db.add(
            UserIntegration(
                user_id=user_id,
                provider="google",
                status=status,
                account_id=account_id,
            )
        )
    else:
        row.status = status
        row.account_id = account_id
    db.commit()


def _register_extra(client, owner, email):
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "pass12345"},
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ingest_account_only_attributed_to_member(client, db, owner, member):
    """n8n-ingested mail carrying only the Gmail account lands on the right user."""
    from app.models.email import Email

    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(account_id="member@google.test", message_id="m-acc-only"),
        headers=_headers(),
    )
    assert r.status_code == 200
    assert r.json()["inserted"] == 1

    row = db.query(Email).filter(Email.message_id == "m-acc-only").first()
    assert row is not None
    assert row.user_id == member["id"]


def test_ingest_account_only_unknown_rejected(client, owner):
    r = client.post(
        INGEST_URL,
        json=_batch(account_id="nobody@google.test", message_id="m-unk"),
        headers=_headers(),
    )
    assert r.status_code == 404


def test_ingest_account_only_ambiguous_rejected(client, db, owner, member):
    """The same Gmail account connected by two users is ambiguous -> 409."""
    from app.models.email import Email

    _seed_google_integration(db, member["id"], account_id="shared@google.test")
    other_id = _register_extra(client, owner, "other@aichief.test")
    _seed_google_integration(db, other_id, account_id="Shared@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(account_id="shared@google.test", message_id="m-amb"),
        headers=_headers(),
    )
    assert r.status_code == 409
    assert db.query(Email).count() == 0


def test_ingest_user_plus_account_mismatch_still_403(client, db, owner, member):
    """Explicit user_id + a different account keeps the strict 403 semantics."""
    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(user_id=member["id"], account_id="other@google.test", message_id="m-ms"),
        headers=_headers(),
    )
    assert r.status_code == 403


def test_agent_accounts_requires_key(client, db, owner, member):
    _seed_google_integration(db, member["id"], account_id="member@google.test")

    assert client.get(AGENT_URL + "/accounts").status_code == 401
    assert (
        client.get(AGENT_URL + "/accounts", headers={"X-API-Key": "wrong"}).status_code == 401
    )

    r = client.get(AGENT_URL + "/accounts", headers=_headers())
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["account_id"] == "member@google.test"
    assert r.json()[0]["user_id"] == member["id"]
    assert r.json()[0]["email"] == "member@aichief.test"


def test_agent_accounts_lists_only_active_google_integrations(client, db, owner, member):
    _seed_google_integration(db, member["id"], status="revoked")
    r = client.get(AGENT_URL + "/accounts", headers=_headers())
    assert r.status_code == 200
    assert r.json() == []

    _seed_google_integration(db, member["id"], status="active", account_id="member@google.test")
    r = client.get(AGENT_URL + "/accounts", headers=_headers())
    assert len(r.json()) == 1


def test_agent_sync_resolves_account_to_the_owning_user(client, db, owner, member, monkeypatch):
    """/sync triggers the per-user Gmail sync under the resolved user's creds."""
    _seed_google_integration(db, member["id"], account_id="member@google.test")

    calls = {}

    async def _fake_sync(max_results=50, db=None, user_id=None):
        calls["user_id"] = user_id
        calls["max_results"] = max_results
        return [1001, 1002], []  # (added_ids, failed_ids)

    monkeypatch.setattr("app.services.gmail_service.sync_emails", _fake_sync)

    r = client.post(
        SYNC_URL,
        json={"account_id": "member@google.test", "limit": 25},
        headers=_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == member["id"]
    assert body["account_id"] == "member@google.test"
    assert body["added"] == 2
    assert body["failed"] == 0
    assert calls == {"user_id": member["id"], "max_results": 25}


def test_agent_sync_unknown_account_404(client, db, owner, monkeypatch):
    async def _fake_sync(**kwargs):
        raise AssertionError("sync must not run for an unknown account")

    monkeypatch.setattr("app.services.gmail_service.sync_emails", _fake_sync)

    r = client.post(SYNC_URL, json={"account_id": "ghost@google.test"}, headers=_headers())
    assert r.status_code == 404


def test_agent_sync_requires_key(client, db, owner, member):
    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(SYNC_URL, json={"account_id": "member@google.test"})
    assert r.status_code == 401


def test_account_only_resolution_never_uses_owner_fallback(client, db, owner, member):
    """A member account supplied WITHOUT user_id must NOT fall back to owner."""
    from app.models.email import Email

    _seed_google_integration(db, member["id"], account_id="member@google.test")

    r = client.post(
        INGEST_URL,
        json=_batch(account_id="member@google.test", message_id="m-no-fallback"),
        headers=_headers(),
    )
    assert r.status_code == 200
    row = db.query(Email).filter(Email.message_id == "m-no-fallback").first()
    assert row.user_id == member["id"]
    assert db.query(Email).filter(Email.user_id == owner["id"]).count() == 0


def test_agent_accounts_scope_self_returns_single_account(client, db, owner, member):
    """scope=self (default) returns only the first active account, not all."""
    _seed_google_integration(db, member["id"], account_id="member@google.test")
    other_id = _register_extra(client, owner, "other@aichief.test")
    _seed_google_integration(db, other_id, account_id="other@google.test")

    r = client.get(AGENT_URL + "/accounts", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["account_id"] in ("member@google.test", "other@google.test")


def test_agent_accounts_scope_all_requires_admin_key(client, db, owner, member):
    """scope=all returns every active account but requires X-Admin-Key."""
    _seed_google_integration(db, member["id"], account_id="member@google.test")
    other_id = _register_extra(client, owner, "other@aichief.test")
    _seed_google_integration(db, other_id, account_id="other@google.test")

    # Without admin key -> 403
    r = client.get(AGENT_URL + "/accounts?scope=all", headers=_headers())
    assert r.status_code == 403

    # With wrong admin key -> 403
    r = client.get(
        AGENT_URL + "/accounts?scope=all",
        headers={**_headers(), "X-Admin-Key": "wrong"},
    )
    assert r.status_code == 403

    # With correct admin key -> 200, all accounts
    r = client.get(
        AGENT_URL + "/accounts?scope=all",
        headers={**_headers(), "X-Admin-Key": settings.ADMIN_API_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    account_ids = {a["account_id"] for a in body}
    assert account_ids == {"member@google.test", "other@google.test"}