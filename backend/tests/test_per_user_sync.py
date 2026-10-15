"""STEP 11: per-user Google sync isolation.

These tests verify that with two distinct users (owner/member) each carrying
their own Google integration and credentials, every sync path stamps rows with
the correct user_id, a failure for one user never blocks the other, and AI /
reminder writes stay owned by the entity's user.

All Google / Classroom service calls are replaced with fake services; no real
network or token state is needed.
"""

import asyncio
import base64
import datetime

from tests.conftest import auth


# --------------------------------------------------------------------------- #
# Fake Google (Gmail) service helpers
# --------------------------------------------------------------------------- #
class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def _full_message(mid, meta):
    return {
        "id": mid,
        "threadId": "t-" + mid,
        "labelIds": [],
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bob@example.com"},
                {"name": "Subject", "value": meta.get("subject", "No subject")},
                {"name": "Date", "value": "Thu, 01 Jan 2026 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {
                "data": base64.urlsafe_b64encode(b"meeting on friday").decode()
            },
        },
    }


def _install_gmail_fakes(monkeypatch, messages_by_id):
    """messages_by_id: {message_id: {"subject": ..., }}

    Makes gmail_service use in-memory services so sync_emails can run with no
    network. Supports pagination via pageToken.
    """
    ordered = list(messages_by_id)

    class _Exec:
        def __init__(self, payload):
            self._payload = payload
        def execute(self):
            return self._payload

    class _Msgs:
        def list(self, userId=None, maxResults=None, pageToken=None):
            # Return all messages on first page (pageToken=None), empty on subsequent
            if pageToken is None:
                return _Exec({"messages": [{"id": mid} for mid in ordered]})
            return _Exec({"messages": []})

        def get(self, userId=None, id=None, format=None):
            return _Exec(_full_message(id, messages_by_id[id]))

    class _Users:
        def messages(self):
            return _Msgs()

    class _Service:
        def users(self):
            return _Users()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    def _fake_thread_service(creds):
        return _Service()

    monkeypatch.setattr("app.services.gmail_service._get_user_service", _fake_user_service)
    monkeypatch.setattr("app.services.gmail_service._service_for_thread", _fake_thread_service)
    monkeypatch.setattr(
        "app.services.gmail_service.credentials_for_user",
        lambda *a, **k: object(),
    )


# --------------------------------------------------------------------------- #
# Fake Classroom service helpers
# --------------------------------------------------------------------------- #
def _install_classroom_fakes(monkeypatch, course_id="123456789"):
    coursework = [
        {
            "id": "55555",
            "courseId": course_id,
            "title": "HW 1",
            "description": "solve everything",
            "workType": "ASSIGNMENT",
            "state": "PUBLISHED",
            "maxPoints": 10,
            "dueDate": {"year": 2026, "month": 2, "day": 10},
            "dueTime": {"hours": 17, "minutes": 0},
        }
    ]

    class _CW:
        def list(self, courseId=None, courseWorkStates=None, pageSize=None, pageToken=None):
            return _Exec({"courseWork": coursework})

    class _C:
        def courseWork(self):
            return _CW()

    class _Service:
        def courses(self):
            return _C()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    async def _fake_courses(limit=100, max_pages=None, db=None, user_id=None):
        return [{"id": course_id, "name": "Math", "section": "A", "room": "1"}]

    async def _fake_submissions(coursework_ids=None, max_pages_per_course=None, db=None, user_id=None):
        return {}

    monkeypatch.setattr(
        "app.services.classroom_service._get_user_service", _fake_user_service
    )
    monkeypatch.setattr("app.services.classroom_service.list_courses", _fake_courses)
    monkeypatch.setattr(
        "app.services.classroom_service.list_submissions", _fake_submissions
    )
    monkeypatch.setattr(
        "app.services.classroom_service.CURRENT_SEMESTER_COURSE_IDS", {course_id}
    )
    # list_assignments re-reads creds to enforce the coursework scope; give it a
    # token that carries it so the sync reaches the (faked) API calls.
    import types

    from app.services.classroom_service import CLASSROOM_COURSEWORK_SCOPE

    creds = types.SimpleNamespace(scopes=[CLASSROOM_COURSEWORK_SCOPE])
    monkeypatch.setattr(
        "app.services.classroom_service.credentials_for_user", lambda *a, **k: creds
    )


async def _sync_classroom(db, user_id):
    from app.services.classroom_service import sync_assignments

    return await sync_assignments(db=db, user_id=user_id)


# --------------------------------------------------------------------------- #
# 1. Google integration rows are scoped per user
# --------------------------------------------------------------------------- #
def test_google_integration_row_scoped_to_owner(client, db, owner, member):
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            account_id="admin@aichief.test",
        )
    )
    db.commit()

    owner_list = client.get("/api/integrations/user", headers=auth(owner["token"]))
    assert owner_list.status_code == 200
    assert any(c["provider"] == "google" for c in owner_list.json())

    member_list = client.get("/api/integrations/user", headers=auth(member["token"]))
    assert member_list.status_code == 200
    assert [c for c in member_list.json() if c["provider"] == "google"] == []

    r = client.delete("/api/integrations/user/google", headers=auth(member["token"]))
    assert r.status_code in (403, 404)

    still = client.get("/api/integrations/user", headers=auth(owner["token"])).json()
    assert any(c["provider"] == "google" for c in still)


# --------------------------------------------------------------------------- #
# 2. _google_connected_user_ids: filter + owner fallback
# --------------------------------------------------------------------------- #
def test_connected_user_ids_fall_back_to_owner(db, owner):
    from app.main import _google_connected_user_ids

    assert _google_connected_user_ids(db) == [owner["id"]]


def test_connected_user_ids_only_active_google(db, owner, member):
    from app.main import _google_connected_user_ids
    from app.models.user_integration import UserIntegration

    db.add(UserIntegration(user_id=owner["id"], provider="google", status="active"))
    db.add(UserIntegration(user_id=member["id"], provider="gmail", status="active"))
    db.add(UserIntegration(user_id=member["id"], provider="google", status="revoked"))
    db.commit()

    ids = _google_connected_user_ids(db)
    assert ids == [owner["id"]]


# --------------------------------------------------------------------------- #
# 3. Gmail sync stamps + dedupes per user
# --------------------------------------------------------------------------- #
def test_gmail_sync_stamps_and_dedupes_per_user(db, owner, member, monkeypatch):
    from app.models.email import Email
    from app.services.gmail_service import sync_emails

    _install_gmail_fakes(monkeypatch, {"m-owner-1": {"subject": "Owner meeting"}})
    added_ids, failed_ids = asyncio.run(sync_emails(max_results=10, db=db, user_id=owner["id"]))
    assert len(added_ids) == 1
    assert failed_ids == []

    rows = db.query(Email).all()
    assert len(rows) == 1
    assert rows[0].user_id == owner["id"]
    assert rows[0].subject == "Owner meeting"

    _install_gmail_fakes(monkeypatch, {"m-member-1": {"subject": "Member meeting"}})
    added_ids, failed_ids = asyncio.run(sync_emails(max_results=10, db=db, user_id=member["id"]))
    assert len(added_ids) == 1
    assert failed_ids == []

    rows = db.query(Email).all()
    assert len(rows) == 2
    by_user = {r.user_id: r.subject for r in rows}
    assert by_user[owner["id"]] == "Owner meeting"
    assert by_user[member["id"]] == "Member meeting"

    _install_gmail_fakes(monkeypatch, {"m-owner-1": {"subject": "Owner meeting"}})
    added_ids, failed_ids = asyncio.run(sync_emails(max_results=10, db=db, user_id=owner["id"]))
    assert added_ids == []
    assert failed_ids == []
    assert db.query(Email).count() == 2


def test_gmail_sync_paginates_all_pages(db, owner, monkeypatch):
    """fetch_recent_emails must follow nextPageToken across multiple pages."""
    from app.models.email import Email
    from app.services.gmail_service import sync_emails

    # 3 pages of 2 messages each = 6 total
    all_ids = [f"m-pag-{i}" for i in range(6)]
    pages = {
        None: {"messages": [{"id": all_ids[0]}, {"id": all_ids[1]}], "nextPageToken": "tok1"},
        "tok1": {"messages": [{"id": all_ids[2]}, {"id": all_ids[3]}], "nextPageToken": "tok2"},
        "tok2": {"messages": [{"id": all_ids[4]}, {"id": all_ids[5]}], "nextPageToken": None},
    }

    class _Exec:
        def __init__(self, payload):
            self._payload = payload
        def execute(self):
            return self._payload

    class _Msgs:
        def list(self, userId=None, maxResults=None, pageToken=None):
            return _Exec(pages.get(pageToken, {"messages": []}))
        def get(self, userId=None, id=None, format=None):
            return _Exec(_full_message(id, {"subject": f"subj-{id}"}))

    class _Users:
        def messages(self):
            return _Msgs()

    class _Service:
        def users(self):
            return _Users()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    def _fake_thread_service(creds):
        return _Service()

    monkeypatch.setattr("app.services.gmail_service._get_user_service", _fake_user_service)
    monkeypatch.setattr("app.services.gmail_service._service_for_thread", _fake_thread_service)
    monkeypatch.setattr(
        "app.services.gmail_service.credentials_for_user",
        lambda *a, **k: object(),
    )

    added_ids, failed_ids = asyncio.run(sync_emails(max_results=50, db=db, user_id=owner["id"]))
    assert len(added_ids) == 6
    assert failed_ids == []
    rows = db.query(Email).all()
    assert len(rows) == 6
    assert {r.message_id for r in rows} == set(all_ids)
    assert len(rows) == 6
    assert {r.message_id for r in rows} == set(all_ids)


def test_gmail_sync_respects_max_messages_cap(db, owner, monkeypatch):
    """max_messages caps total fetched even if more pages exist."""
    from app.models.email import Email
    from app.services.gmail_service import sync_emails

    all_ids = [f"m-cap-{i}" for i in range(10)]
    pages = {
        None: {"messages": [{"id": all_ids[0]}, {"id": all_ids[1]}], "nextPageToken": "tok1"},
        "tok1": {"messages": [{"id": all_ids[2]}, {"id": all_ids[3]}], "nextPageToken": "tok2"},
        "tok2": {"messages": [{"id": all_ids[4]}, {"id": all_ids[5]}], "nextPageToken": None},
    }

    class _Exec:
        def __init__(self, payload):
            self._payload = payload
        def execute(self):
            return self._payload

    class _Msgs:
        def list(self, userId=None, maxResults=None, pageToken=None):
            return _Exec(pages.get(pageToken, {"messages": []}))
        def get(self, userId=None, id=None, format=None):
            return _Exec(_full_message(id, {"subject": f"subj-{id}"}))

    class _Users:
        def messages(self):
            return _Msgs()

    class _Service:
        def users(self):
            return _Users()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    def _fake_thread_service(creds):
        return _Service()

    monkeypatch.setattr("app.services.gmail_service._get_user_service", _fake_user_service)
    monkeypatch.setattr("app.services.gmail_service._service_for_thread", _fake_thread_service)
    monkeypatch.setattr(
        "app.services.gmail_service.credentials_for_user",
        lambda *a, **k: object(),
    )

    added_ids, failed_ids = asyncio.run(sync_emails(max_results=50, max_messages=3, db=db, user_id=owner["id"]))
    assert len(added_ids) == 3
    assert failed_ids == []
    assert db.query(Email).count() == 3


# --------------------------------------------------------------------------- #
# 4. Classroom sync stamps + dedupes per user
# --------------------------------------------------------------------------- #
def test_classroom_sync_stamps_per_user(db, owner, member, monkeypatch):
    from app.models.assignment import Assignment
    from app.models.event import Event
    from app.models.task import Task

    _install_classroom_fakes(monkeypatch)
    res = asyncio.run(_sync_classroom(db, member["id"]))
    assert res["tasks_created"] == 1
    assert res["events_created"] == 1
    assert res["assignments_created"] == 1

    asyncio.run(_sync_classroom(db, owner["id"]))

    tasks = db.query(Task).filter(Task.source == "classroom").all()
    assert len(tasks) == 2
    assert {t.user_id for t in tasks} == {owner["id"], member["id"]}
    assert all(t.source_id == 55555 for t in tasks)

    events = db.query(Event).filter(Event.source == "classroom").all()
    assert len(events) == 2
    assert {e.user_id for e in events} == {owner["id"], member["id"]}

    assignments = db.query(Assignment).filter(Assignment.source == "classroom").all()
    assert len(assignments) == 2
    assert {a.user_id for a in assignments} == {owner["id"], member["id"]}


def test_classroom_sync_is_idempotent_per_user(db, owner, monkeypatch):
    from app.models.assignment import Assignment
    from app.models.event import Event
    from app.models.task import Task

    _install_classroom_fakes(monkeypatch)
    asyncio.run(_sync_classroom(db, owner["id"]))
    res = asyncio.run(_sync_classroom(db, owner["id"]))

    assert res["tasks_created"] == 0
    assert res["events_created"] == 0
    assert res["assignments_created"] == 0
    assert db.query(Task).filter(Task.source == "classroom").count() == 1
    assert db.query(Event).filter(Event.source == "classroom").count() == 1
    assert db.query(Assignment).filter(Assignment.source == "classroom").count() == 1


# --------------------------------------------------------------------------- #
# 5 + 6. Background sync passes: one user's failure never blocks the other
# --------------------------------------------------------------------------- #
def _seed_active_google_integrations(db, user_ids):
    from app.models.user_integration import UserIntegration

    for uid in user_ids:
        db.add(UserIntegration(user_id=uid, provider="google", status="active"))
    db.commit()


def test_background_gmail_pass_isolates_user_failures(db, owner, member, monkeypatch):
    from app.main import _sync_emails_pass

    _seed_active_google_integrations(db, [owner["id"], member["id"]])

    attempted = []

    async def _fake_sync(max_results=25, db=None, user_id=None):
        attempted.append(user_id)
        if user_id == owner["id"]:
            raise RuntimeError("owner's token was revoked")
        return [99], []  # (added_ids, failed_ids)

    monkeypatch.setattr(
        "app.services.google_auth.credentials_for_user", lambda *a, **k: object()
    )
    monkeypatch.setattr("app.services.gmail_service.sync_emails", _fake_sync)

    try:
        added = _sync_emails_pass(db)
    finally:
        db.rollback()

    # Both users attempted sync; the owner's failure did not stop the member.
    assert owner["id"] in attempted
    assert member["id"] in attempted
    assert added == [99]


def test_background_classroom_pass_isolates_user_failures(db, owner, member, monkeypatch):
    from app.main import _sync_classroom_pass

    _seed_active_google_integrations(db, [owner["id"], member["id"]])

    submission_calls = []

    async def _fake_status(db=None, user_id=None):
        submission_calls.append(user_id)
        if user_id == owner["id"]:
            raise RuntimeError("classroom API errored")

    async def _fake_assignments(db=None, user_id=None):
        return {}

    monkeypatch.setattr(
        "app.services.google_auth.credentials_for_user", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "app.services.classroom_service.sync_submission_status", _fake_status
    )
    monkeypatch.setattr(
        "app.services.classroom_service.sync_assignments", _fake_assignments
    )

    try:
        _sync_classroom_pass(db)
    finally:
        db.rollback()

    assert owner["id"] in submission_calls
    assert member["id"] in submission_calls


# --------------------------------------------------------------------------- #
# 7. AI schedule extraction retains the email's owner
# --------------------------------------------------------------------------- #
def test_email_processor_schedules_to_email_owner(db, owner, member, monkeypatch):
    from app.models.email import Email
    from app.models.event import Event
    from app.services.ai_service import ai_service
    from app.services.email_processor import process_email_sync

    now = datetime.datetime.utcnow()

    async def _fake_analyze(subject="", body=""):
        return {"category": "meeting", "summary": "standup"}

    async def _fake_schedule(subject="", body=""):
        return {
            "items": [
                {
                    "kind": "event",
                    "title": "Feedback sync",
                    "date": now.strftime("%Y-%m-%d"),
                    "time": "14:00",
                }
            ]
        }

    monkeypatch.setattr(ai_service, "analyze_email", _fake_analyze)
    monkeypatch.setattr(ai_service, "extract_schedule", _fake_schedule)

    owner_email = Email(
        user_id=owner["id"],
        message_id="mail-owner-1",
        thread_id="t-owner",
        from_email="someone@example.com",
        subject=f"Review on {now.strftime('%Y-%m-%d')}",
        body_text="please look",
        received_at=now,
    )
    member_email = Email(
        user_id=member["id"],
        message_id="mail-member-1",
        thread_id="t-member",
        from_email="someone@example.com",
        subject=f"Review on {now.strftime('%Y-%m-%d')}",
        body_text="please look",
        received_at=now,
    )
    db.add_all([owner_email, member_email])
    db.commit()
    db.refresh(owner_email)
    db.refresh(member_email)

    r1 = process_email_sync(owner_email.id)
    r2 = process_email_sync(member_email.id)

    assert r1["user_id"] == owner["id"]
    assert r2["user_id"] == member["id"]
    assert all(o["action"] == "created" for o in r1["scheduled_items"])
    assert all(o["action"] == "created" for o in r2["scheduled_items"])

    events = (
        db.query(Event)
        .filter(Event.title == "Feedback sync", Event.source == "email")
        .all()
    )
    assert len(events) == 2
    assert {e.user_id for e in events} == {owner["id"], member["id"]}


# --------------------------------------------------------------------------- #
# 8. Reminder notifications are owned by the entity's user
# --------------------------------------------------------------------------- #
def test_reminder_notifications_owned_by_entity_user(db, owner, member):
    from app.models.event import Event
    from app.models.notification import Notification
    from app.models.task import Task
    from app.services.reminder_service import generate_reminders

    now = datetime.datetime.utcnow()
    ev = Event(
        user_id=member["id"],
        title="Member sync",
        start_time=now + datetime.timedelta(hours=2),
        end_time=now + datetime.timedelta(hours=3),
        source="test",
        is_all_day=False,
    )
    task = Task(
        user_id=owner["id"],
        title="Owner pending task",
        due_date=now + datetime.timedelta(hours=20),
        source="test",
        status="pending",
    )
    db.add_all([ev, task])
    db.commit()
    db.refresh(ev)
    db.refresh(task)

    created = generate_reminders(force=True)
    assert created >= 2

    event_n = (
        db.query(Notification)
        .filter(
            Notification.entity_type == "event",
            Notification.entity_id == ev.id,
        )
        .first()
    )
    task_n = (
        db.query(Notification)
        .filter(
            Notification.entity_type == "task",
            Notification.entity_id == task.id,
        )
        .first()
    )
    assert event_n is not None
    assert event_n.user_id == member["id"]
    assert task_n is not None
    assert task_n.user_id == owner["id"]


# --------------------------------------------------------------------------- #
# 8. Gmail: per-message failure handling without aborting the page
# --------------------------------------------------------------------------- #
def test_gmail_sync_partial_failure_handling(db, owner, monkeypatch):
    """When individual message fetch fails, the page should continue and report failures."""
    from app.models.email import Email
    from app.services.gmail_service import sync_emails

    # 5 messages total: 2 succeed, 2 fail, 1 succeeds
    all_ids = [f"m-fail-{i}" for i in range(5)]
    pages = {
        None: {"messages": [{"id": all_ids[0]}, {"id": all_ids[1]}], "nextPageToken": "tok1"},
        "tok1": {"messages": [{"id": all_ids[2]}, {"id": all_ids[3]}], "nextPageToken": "tok2"},
        "tok2": {"messages": [{"id": all_ids[4]}], "nextPageToken": None},
    }

    class _Exec:
        def __init__(self, payload):
            self._payload = payload
        def execute(self):
            return self._payload

    class _Msgs:
        def list(self, userId=None, maxResults=None, pageToken=None):
            return _Exec(pages.get(pageToken, {"messages": []}))
        def get(self, userId=None, id=None, format=None):
            # Fail for m-fail-1 and m-fail-3
            if id in ("m-fail-1", "m-fail-3"):
                raise RuntimeError("simulated fetch failure")
            return _Exec(_full_message(id, {"subject": f"subj-{id}"}))

    class _Users:
        def messages(self):
            return _Msgs()

    class _Service:
        def users(self):
            return _Users()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    def _fake_thread_service(creds):
        return _Service()

    monkeypatch.setattr("app.services.gmail_service._get_user_service", _fake_user_service)
    monkeypatch.setattr("app.services.gmail_service._service_for_thread", _fake_thread_service)
    monkeypatch.setattr(
        "app.services.gmail_service.credentials_for_user",
        lambda *a, **k: object(),
    )

    added_ids, failed_ids = asyncio.run(sync_emails(max_results=50, db=db, user_id=owner["id"]))

    # 3 messages should succeed (0, 2, 4)
    assert len(added_ids) == 3
    # 2 messages should fail (1, 3)
    assert len(failed_ids) == 2
    assert set(failed_ids) == {"m-fail-1", "m-fail-3"}
    # Total emails in DB should be 3
    assert db.query(Email).count() == 3


# --------------------------------------------------------------------------- #
# 9. Classroom: pagination across multiple pages
# --------------------------------------------------------------------------- #
def test_classroom_list_courses_paginates(db, owner, monkeypatch):
    """list_courses follows nextPageToken across multiple pages."""
    from app.services.classroom_service import list_courses

    all_courses = [{"id": f"c-{i}", "name": f"Course {i}", "section": "A", "room": "101"} for i in range(6)]
    pages = {
        None: {"courses": all_courses[:2], "nextPageToken": "tok1"},
        "tok1": {"courses": all_courses[2:4], "nextPageToken": "tok2"},
        "tok2": {"courses": all_courses[4:6], "nextPageToken": None},
    }

    class _Exec:
        def __init__(self, payload):
            self._payload = payload
        def execute(self):
            return self._payload

    class _C:
        def list(self, pageSize=None, courseStates=None, pageToken=None):
            return _Exec(pages.get(pageToken, {"courses": []}))

    class _Service:
        def courses(self):
            return _C()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    monkeypatch.setattr("app.services.classroom_service._get_user_service", _fake_user_service)
    monkeypatch.setattr("app.services.classroom_service.credentials_for_user", lambda *a, **k: object())

    courses = asyncio.run(list_courses(limit=10, max_pages=5, db=db, user_id=owner["id"]))
    assert len(courses) == 6
    assert {c["id"] for c in courses} == {f"c-{i}" for i in range(6)}


# --------------------------------------------------------------------------- #
# 10. Calendar: pagination in read-before-write
# --------------------------------------------------------------------------- #
def test_calendar_list_existing_events_paginates(db, owner, monkeypatch):
    """_list_existing_events follows nextPageToken to read all events in window."""
    from app.services.calendar_service import _list_existing_events

    # Simulate 600 events across 3 pages (250 + 250 + 100)
    all_events = [{"id": f"evt-{i}", "summary": f"Event {i}", "start": {"dateTime": "2026-01-15T10:00:00Z"}} for i in range(600)]
    pages = {
        None: {"items": all_events[:250], "nextPageToken": "tok1"},
        "tok1": {"items": all_events[250:500], "nextPageToken": "tok2"},
        "tok2": {"items": all_events[500:], "nextPageToken": None},
    }

    class _Exec:
        def __init__(self, payload):
            self._payload = payload
        def execute(self):
            return self._payload

    class _Events:
        def list(self, calendarId=None, timeMin=None, timeMax=None, singleEvents=None, maxResults=None, pageToken=None):
            return _Exec(pages.get(pageToken, {"items": []}))

    class _Service:
        def events(self):
            return _Events()

    async def _fake_user_service(db=None, user_id=None):
        return _Service()

    events = asyncio.run(_list_existing_events(_Service(), "2026-01-15T00:00:00", "2026-01-15T23:59:59", max_pages=5))
    assert len(events) == 600
    assert events[0]["id"] == "evt-0"
    assert events[-1]["id"] == "evt-599"