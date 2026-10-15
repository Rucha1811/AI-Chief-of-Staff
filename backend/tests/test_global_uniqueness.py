"""P2.2: global uniqueness / consistency review.

Scope decided by the P2.2 audit - NOT every unique key became composite:

- ``emails.message_id``                  -> UNIQUE (user_id, message_id)   [0008]
- ``events.calendar_event_id``           -> UNIQUE (user_id, calendar_event_id) [0006]
- ``notifications.fingerprint``          -> UNIQUE (user_id, fingerprint)  [0009]
- ``whatsapp_messages.message_id``       -> GLOBAL (Twilio SID / Meta wamid are
  provider-globally-unique identifiers, not user-derived)
- ``push_subscriptions.endpoint``        -> GLOBAL (one Web Push endpoint is one
  browser registration)
- ``users.email``                        -> GLOBAL, case-insensitive lower(email)

These tests pin all of that down, plus the ownership behaviour that depends on
it: a duplicated provider identifier must never let one user read, update,
delete or re-attribute another user's row.
"""

import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import settings

INGEST_URL = "/api/integrations/emails/ingest"


def _headers():
    return {"X-API-Key": settings.INGEST_API_KEY}


def _batch(account_id=None, message_id="gmail-123"):
    batch = {
        "emails": [
            {
                "message_id": message_id,
                "thread_id": f"t-{message_id}",
                "from_email": f"alice@example.com",
                "subject": "hello",
                "body_text": "body",
                "received_at": "2026-01-01T10:00:00",
            }
        ]
    }
    if account_id is not None:
        batch["account_id"] = account_id
    return batch


def _seed_google_integration(db, user_id, account_id):
    from app.models.user_integration import UserIntegration

    row = (
        db.query(UserIntegration)
        .filter(UserIntegration.user_id == user_id, UserIntegration.provider == "google")
        .first()
    )
    if row is None:
        db.add(UserIntegration(user_id=user_id, provider="google",
                               status="active", account_id=account_id))
    else:
        row.status = "active"
        row.account_id = account_id
    db.commit()


def _register_extra(client, owner, email):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": "extra12345"},
        headers={"Authorization": f"Bearer {owner['token']}"},
    ).json()["id"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A. notifications.fingerprint  ->  UNIQUE (user_id, fingerprint)   [0009]
# ---------------------------------------------------------------------------


def test_same_fingerprint_allowed_for_two_users(db, owner, member):
    """A.1 + A.2: one user's fingerprint must not block another user's."""
    from app.models.notification import Notification

    fp = "event:9001:2026-10-03"
    db.add(Notification(type="event", title="A", message="a", fingerprint=fp,
                        user_id=owner["id"]))
    db.commit()
    db.add(Notification(type="event", title="B", message="b", fingerprint=fp,
                        user_id=member["id"]))
    db.commit()  # would raise IntegrityError under the old GLOBAL index

    rows = db.query(Notification).filter(Notification.fingerprint == fp).all()
    assert {r.user_id for r in rows} == {owner["id"], member["id"]}
    assert len(rows) == 2


def test_same_fingerprint_rejected_for_same_user(db, owner):
    """A.3: the database still prevents a duplicate for the SAME user."""
    from app.models.notification import Notification

    fp = "task:9002:2026-10-03"
    db.add(Notification(type="task", title="A", message="a", fingerprint=fp,
                        user_id=owner["id"]))
    db.commit()
    db.add(Notification(type="task", title="A dup", message="a", fingerprint=fp,
                        user_id=owner["id"]))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(Notification).filter(
        Notification.fingerprint == fp, Notification.user_id == owner["id"]
    ).count() == 1


def test_reminder_service_dedupes_per_user(db, owner, member):
    """A.4: application-level dedup stays per user and still suppresses dupes."""
    from app.models.notification import Notification
    from app.services.reminder_service import generate_reminders

    start = datetime.datetime(2026, 10, 3, 14, 0)
    for uid in (owner["id"], member["id"]):
        db.add(
            __import__("app.models.task", fromlist=["Task"]).Task(
                user_id=uid,
                title=f"Shared task title {uid}",
                status="pending",
                priority="high",
                due_date=start,
            )
        )
    db.commit()

    created_first = generate_reminders(force=True)
    created_second = generate_reminders(force=True)

    rows = db.query(Notification).filter(Notification.type == "task").all()
    # Both users got their own reminder for the same due window...
    assert {r.user_id for r in rows} == {owner["id"], member["id"]}
    # ...and the second pass created nothing (dedup, not duplication).
    assert created_first >= 2
    assert created_second == 0
    for r in rows:
        same = db.query(Notification).filter(
            Notification.user_id == r.user_id, Notification.fingerprint == r.fingerprint
        ).count()
        assert same == 1


def test_null_fingerprints_allowed_for_multiple_rows(db, owner, member):
    """PostgreSQL NULL semantics preserved: NULLs are not 'duplicates'."""
    from app.models.notification import Notification

    for uid in (owner["id"], member["id"]):
        for _ in range(2):
            db.add(Notification(type="info", title="n", message="m",
                                fingerprint=None, user_id=uid))
    db.commit()
    assert db.query(Notification).filter(
        Notification.fingerprint.is_(None)
    ).count() == 4


# ---------------------------------------------------------------------------
# B. whatsapp_messages.message_id  ->  stays GLOBAL
# ---------------------------------------------------------------------------


def test_whatsapp_message_id_remains_globally_unique(db, owner):
    """B.5: provider message ids are globally unique and stay that way."""
    from app.models.whatsapp_message import WhatsappMessage

    db.add(WhatsappMessage(message_id="sid-global-1", direction="in",
                           from_number="+1", to_number="+2", text="a",
                           provider="twilio", user_id=owner["id"]))
    db.commit()
    with pytest.raises(IntegrityError):
        db.add(WhatsappMessage(message_id="sid-global-1", direction="in",
                               from_number="+9", to_number="+8", text="b",
                               provider="twilio", user_id=owner["id"] + 999))
        db.commit()
    db.rollback()


def test_whatsapp_conflict_leaves_session_usable(db, owner, member):
    """B.6: an IntegrityError must not poison the session for later statements."""
    from app.models.whatsapp_message import WhatsappMessage
    from app.services.whatsapp_service import _save

    db.add(WhatsappMessage(message_id="sid-poison", direction="in",
                           from_number="+1", to_number="+2", text="first",
                           provider="twilio", user_id=owner["id"]))
    db.commit()

    with pytest.raises(IntegrityError):
        _save(db, message_id="sid-poison", direction="in", from_number="+3",
              to_number="+4", text="second", provider="twilio",
              user_id=member["id"])

    # Session is still usable: a normal insert and a query both succeed.
    db.add(WhatsappMessage(message_id="sid-after", direction="in",
                           from_number="+5", to_number="+6", text="after",
                           provider="twilio", user_id=member["id"]))
    db.commit()
    assert db.query(WhatsappMessage).filter(
        WhatsappMessage.message_id == "sid-after"
    ).count() == 1


def test_whatsapp_existing_row_never_re_attributed(db, owner, member):
    """B.7: a duplicate id never moves or edits the owning user's row."""
    from app.models.whatsapp_message import WhatsappMessage
    from app.services.whatsapp_service import _save

    db.add(WhatsappMessage(message_id="sid-keep", direction="in",
                           from_number="+1", to_number="+2", text="original",
                           provider="twilio", user_id=owner["id"]))
    db.commit()

    with pytest.raises(IntegrityError):
        _save(db, message_id="sid-keep", direction="in", from_number="+3",
              to_number="+4", text="hijack", provider="twilio",
              user_id=member["id"])
    db.rollback()

    rows = db.query(WhatsappMessage).filter(
        WhatsappMessage.message_id == "sid-keep"
    ).all()
    assert len(rows) == 1
    assert rows[0].user_id == owner["id"]
    assert rows[0].text == "original"
    assert rows[0].from_number == "+1"


def test_whatsapp_same_id_same_owner_is_idempotent(db, owner):
    """Family-level dedup: a provider redelivery is the same row."""
    from app.models.whatsapp_message import WhatsappMessage
    from app.services.whatsapp_service import save_inbound

    first = save_inbound(db, message_id="sid-retry-1", from_number="+1555",
                         to_number="+1333", text="hi", provider="twilio")
    again = save_inbound(db, message_id="sid-retry-1", from_number="+1555",
                         to_number="+1333", text="hi", provider="twilio")
    assert again.id == first.id
    assert db.query(WhatsappMessage).filter(
        WhatsappMessage.message_id == "sid-retry-1"
    ).count() == 1


def test_whatsapp_inbound_routing_is_deterministic(db, owner, member):
    """Two integrations on one number resolve to the lowest id, stably.

    (provider, account_id) intentionally has NO unique constraint, so two
    accounts can point at the same number. The lookup must be deterministic
    instead of depending on planner order, and must not change ownership
    semantics: the first-registered account wins.
    """
    from app.models.user_integration import UserIntegration
    from app.services.whatsapp_service import save_inbound

    first = UserIntegration(user_id=owner["id"], provider="whatsapp",
                            account_id="+15550001111", status="active")
    db.add(first)
    db.commit()
    second = UserIntegration(user_id=member["id"], provider="whatsapp",
                             account_id="+15550001111", status="active")
    db.add(second)
    db.commit()
    assert second.id > first.id

    resolved = [
        save_inbound(db, message_id=f"sid-det-{i}", from_number="+15550001111",
                     to_number="+1333", text="hi", provider="twilio").user_id
        for i in range(3)
    ]
    assert resolved == [owner["id"]] * 3

    # Flipping insertion order flips the winner - proof the ordering (not a
    # lucky row order) decides.
    db.query(UserIntegration).filter(UserIntegration.id == first.id).delete()
    db.commit()
    assert save_inbound(db, message_id="sid-det-9", from_number="+15550001111",
                        to_number="+1333", text="hi", provider="twilio").user_id == member["id"]


# ---------------------------------------------------------------------------
# C. push_subscriptions.endpoint  ->  stays GLOBAL, ownership is rebound
# ---------------------------------------------------------------------------


def _sub_payload(endpoint):
    """A syntactically valid subscription: p256dh decodes to 65 bytes, auth to 16."""
    import base64

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "endpoint": endpoint,
        "keys": {"p256dh": b64(bytes(65)), "auth": b64(bytes(16))},
    }


def test_push_endpoint_resubscribed_by_other_user_rebinds_ownership(db, owner, member):
    """C.8 + C.11: ownership moves to the current user; no duplicate row."""
    from app.models.push_subscription import PushSubscription
    from app.services.push_service import save_subscription

    assert save_subscription(_sub_payload("https://push/shared-browser"),
                             user_id=owner["id"]) is True
    assert save_subscription(_sub_payload("https://push/shared-browser"),
                             user_id=member["id"]) is True

    rows = db.query(PushSubscription).filter(
        PushSubscription.endpoint == "https://push/shared-browser"
    ).all()
    assert len(rows) == 1          # C.11 global uniqueness, no duplicate row
    assert rows[0].user_id == member["id"]   # C.8 rebound to the current user


def test_push_unauthenticated_caller_cannot_rebind_ownership(db, owner, member):
    """A caller with no authenticated user must not move an endpoint."""
    from app.models.push_subscription import PushSubscription
    from app.services.push_service import save_subscription

    save_subscription(_sub_payload("https://push/no-anon"), user_id=owner["id"])
    save_subscription(_sub_payload("https://push/no-anon"), user_id=None)

    row = db.query(PushSubscription).filter(
        PushSubscription.endpoint == "https://push/no-anon"
    ).one()
    assert row.user_id == owner["id"]


def test_push_delivery_follows_rebound_endpoint(db, owner, member, monkeypatch):
    """C.9 + C.10: after the rebind, only the new owner is delivered."""
    from app.models.notification import Notification
    from app.services import push_service

    push_service.save_subscription(_sub_payload("https://push/handover"),
                                   user_id=owner["id"])
    push_service.save_subscription(_sub_payload("https://push/handover"),
                                   user_id=member["id"])

    db.add(Notification(user_id=member["id"], type="task", title="B alert",
                        message="b", priority="high", fingerprint="fp-handover-b"))
    db.commit()

    sent = []
    monkeypatch.setattr(
        push_service, "_send",
        lambda sub, title, body, et, eid: sent.append((sub.user_id, title)) or True,
    )
    push_service._last_send_ts = 0.0
    push_service.push_pending_notifications()

    assert (member["id"], "B alert") in sent
    assert all(uid == member["id"] for uid, _ in sent)


def test_push_rebind_end_to_end_through_authenticated_route(client, db, owner, member):
    """C.8-C.11 over HTTP: /subscribe is auth-gated and rebinds the endpoint."""
    from app.models.push_subscription import PushSubscription

    payload = _sub_payload("https://push/http-handover")

    r_a = client.post("/api/notifications/subscribe", json=payload,
                      headers=auth(owner["token"]))
    assert r_a.status_code == 200 and r_a.json() == {"ok": True}
    assert db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload["endpoint"]
    ).one().user_id == owner["id"]

    # Unauthenticated callers cannot create or rebind a subscription at all.
    r_anon = client.post("/api/notifications/subscribe", json=payload)
    assert r_anon.status_code in (401, 403)
    assert db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload["endpoint"]
    ).one().user_id == owner["id"]

    # The same browser, now signed in as B, hands the endpoint over.
    r_b = client.post("/api/notifications/subscribe", json=payload,
                      headers=auth(member["token"]))
    assert r_b.status_code == 200 and r_b.json() == {"ok": True}

    rows = db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload["endpoint"]
    ).all()
    assert len(rows) == 1
    assert rows[0].user_id == member["id"]   # A no longer owns it


# ---------------------------------------------------------------------------
# D. model metadata matches the migrated schema
# ---------------------------------------------------------------------------


def test_model_declares_case_insensitive_unique_email(db_engine):
    """D.12: users.email uniqueness is case-insensitive lower(email)."""
    from app.models.user import User

    indexes = {ix.name: ix for ix in User.__table__.indexes}
    assert "ux_users_email" in indexes
    idx = indexes["ux_users_email"]
    assert idx.unique is True
    rendered = str(idx.expressions[0]).lower()
    assert "lower" in rendered and "email" in rendered

    # ...and the schema create_all actually built carries the same key.
    # Expression indexes are reflected with column_names=[None], so read the
    # server's own definition.
    from sqlalchemy import text

    with db_engine.connect() as conn:
        ddl = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='users' "
                "AND indexname='ux_users_email'"
            )
        ).scalar()
    assert ddl is not None
    assert "UNIQUE INDEX" in ddl
    assert "lower" in ddl.lower() and "email" in ddl.lower()

    # Case-insensitivity is real: two accounts differing only in case collide.
    from sqlalchemy.exc import IntegrityError
    from tests.conftest import SessionLocal

    s = SessionLocal()
    try:
        s.add(User(email="Case@Example.com", password_hash="x"))
        s.commit()
        s.add(User(email="case@example.COM", password_hash="x"))
        with pytest.raises(IntegrityError):
            s.commit()
    finally:
        s.rollback()
        s.close()


def test_model_declares_unique_google_auth_state_per_user(db_engine):
    """D.13: exactly one google_auth_state row per user, as in the DB."""
    from sqlalchemy import inspect

    from app.models.google_auth_state import GoogleAuthState

    unique_column_sets = [
        {c.name for c in uc.columns} for uc in GoogleAuthState.__table__.constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"user_id"} in unique_column_sets

    live = {ix["name"]: ix for ix in inspect(db_engine).get_indexes("google_auth_state")}
    assert live["ux_google_auth_state_user"]["unique"] is True
    assert live["ux_google_auth_state_user"]["column_names"] == ["user_id"]


def test_model_declares_per_user_notification_fingerprint(db_engine):
    """The 0009 constraint is declared in the model, not only in Alembic."""
    from sqlalchemy import inspect

    from app.models.notification import Notification

    unique_column_sets = [
        {c.name for c in uc.columns} for uc in Notification.__table__.constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"user_id", "fingerprint"} in unique_column_sets
    assert not any(
        {c.name for c in uc.columns} == {"fingerprint"}
        for uc in Notification.__table__.constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    )

    live = {
        ix["name"]: ix
        for ix in inspect(db_engine).get_indexes("notifications")
        if "fingerprint" in ix["name"]
    }
    assert list(live) == ["ux_notifications_user_fingerprint"]
    assert live["ux_notifications_user_fingerprint"]["unique"] is True
    assert live["ux_notifications_user_fingerprint"]["column_names"] == [
        "user_id",
        "fingerprint",
    ]


# ---------------------------------------------------------------------------
# E. cross-user isolation must not depend on identifier uniqueness
# ---------------------------------------------------------------------------


def test_user_b_cannot_touch_user_a_notification_by_fingerprint(client, db, owner, member):
    """E.14 + E.15: knowing a fingerprint grants no access to A's row."""
    from app.models.notification import Notification

    fp = "event:7777:2026-10-03"
    db.add(Notification(type="event", title="A private", message="secret",
                        fingerprint=fp, priority="high", user_id=owner["id"]))
    db.commit()
    a_id = db.query(Notification.id).filter(
        Notification.user_id == owner["id"], Notification.fingerprint == fp
    ).scalar()

    b = client.get("/api/notifications", headers=auth(member["token"]))
    assert b.status_code == 200
    assert [n["title"] for n in b.json()] == []          # B cannot list it
    assert client.post(f"/api/notifications/{a_id}/read",
                       headers=auth(member["token"])).json() == {"ok": False}
    assert client.post("/api/notifications/read-all",
                       headers=auth(member["token"])).json() == {"ok": True}

    db.expire_all()
    row = db.query(Notification).filter(Notification.id == a_id).one()
    assert row.user_id == owner["id"]
    assert row.is_read is False
    assert row.title == "A private"


def test_user_b_cannot_touch_user_a_email_by_duplicate_message_id(client, db, owner, member):
    """A duplicated provider id never lets User B reach/update User A's row."""
    from app.models.email import Email

    other_id = _register_extra(client, owner, "other@aichief.test")
    _seed_google_integration(db, member["id"], "member@google.test")
    _seed_google_integration(db, other_id, "other@google.test")

    client.post(
        INGEST_URL, json=_batch(account_id="member@google.test", message_id="gmail-3"),
        headers=_headers(),
    )
    client.post(
        INGEST_URL, json=_batch(account_id="other@google.test", message_id="gmail-3"),
        headers=_headers(),
    )
    a_id = (
        db.query(Email.id).filter(Email.user_id == member["id"], Email.message_id == "gmail-3")
    ).scalar()
    b_token = client.post("/api/auth/login",
                          json={"email": "other@aichief.test", "password": "extra12345"}).json()["token"]

    assert client.get(f"/api/emails/{a_id}", headers=auth(b_token)).status_code == 404
    r = client.patch(
        f"/api/emails/{a_id}/category", json={"category": "important"},
        headers=auth(b_token),
    )
    assert r.status_code == 404
    assert db.query(Email).filter(Email.id == a_id, Email.category == "unknown").count() == 1


# ---------------------------------------------------------------------------
# emails.message_id (0008) / events.calendar_event_id (0006) unchanged
# ---------------------------------------------------------------------------


def test_same_message_id_can_exist_for_two_users(client, db, owner, member):
    from app.models.email import Email

    other_id = _register_extra(client, owner, "other@aichief.test")
    _seed_google_integration(db, member["id"], "member@google.test")
    _seed_google_integration(db, other_id, "other@google.test")

    r1 = client.post(
        INGEST_URL, json=_batch(account_id="member@google.test", message_id="gmail-1"),
        headers=_headers(),
    )
    r2 = client.post(
        INGEST_URL, json=_batch(account_id="other@google.test", message_id="gmail-1"),
        headers=_headers(),
    )
    assert r1.status_code == 200 and r1.json()["inserted"] == 1
    assert r2.status_code == 200 and r2.json()["inserted"] == 1

    rows = db.query(Email).filter(Email.message_id == "gmail-1").all()
    assert {r.user_id for r in rows} == {member["id"], other_id}
    assert len(rows) == 2


def test_same_message_id_same_user_deduplicated(client, db, owner, member):
    from app.models.email import Email

    _seed_google_integration(db, member["id"], "member@google.test")

    r1 = client.post(
        INGEST_URL, json=_batch(account_id="member@google.test", message_id="gmail-2"),
        headers=_headers(),
    )
    r2 = client.post(
        INGEST_URL, json=_batch(account_id="member@google.test", message_id="gmail-2"),
        headers=_headers(),
    )
    assert r1.json()["inserted"] == 1
    assert r2.json()["inserted"] == 0
    assert r2.json()["ignored"] == 1
    assert db.query(Email).filter(Email.message_id == "gmail-2").count() == 1


def test_same_calendar_event_id_can_exist_for_two_users(db, owner, member):
    """Classroom coursework ids are shared by classmates; scope is per user."""
    from app.models.event import Event

    when = datetime.datetime(2026, 10, 5, 9, 0)
    for uid in (owner["id"], member["id"]):
        db.add(Event(calendar_event_id="cw-42", title="Coursework", start_time=when,
                     end_time=when + datetime.timedelta(hours=1),
                     source="classroom", user_id=uid))
    db.commit()
    assert db.query(Event).filter(Event.calendar_event_id == "cw-42").count() == 2


def test_gmail_sync_dedup_is_user_scoped(client, db, owner, member, monkeypatch):
    """Native Gmail sync dedups per user; same id across users both land."""
    from app.services.gmail_service import sync_emails

    other_id = _register_extra(client, owner, "other@aichief.test")

    async def _fake_fetch(max_results=50, db=None, user_id=None, max_messages=None):
        return [
            {
                "message_id": "gmail-sync-1",
                "thread_id": "t-1",
                "from_email": "alice@example.com",
                "from_name": "Alice",
                "to_email": "me@example.com",
                "subject": "hi",
                "body_text": "body",
                "received_at": datetime.datetime(2026, 1, 1, 10, 0, 0),
                "is_read": False,
            }
        ], []

    monkeypatch.setattr("app.services.gmail_service.fetch_recent_emails", _fake_fetch)

    added_a, _ = asyncio_run(sync_emails(max_results=5, db=db, user_id=member["id"]))
    added_b, _ = asyncio_run(sync_emails(max_results=5, db=db, user_id=other_id))
    added_a2, _ = asyncio_run(sync_emails(max_results=5, db=db, user_id=member["id"]))

    from app.models.email import Email

    rows = db.query(Email).filter(Email.message_id == "gmail-sync-1").all()
    assert {r.user_id for r in rows} == {member["id"], other_id}
    assert added_a == [r.id for r in rows if r.user_id == member["id"]]
    assert added_b == [r.id for r in rows if r.user_id == other_id]
    assert added_a2 == []
