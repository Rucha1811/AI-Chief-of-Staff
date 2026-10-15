import datetime

from tests.conftest import auth


def _seed_owner_rows(db, owner_id: int, client):
    """Insert one row owned by `owner_id` in each user-owned table."""
    from app.models.assignment import Assignment
    from app.models.document import Document
    from app.models.email import Email
    from app.models.event import Event
    from app.models.exam import Exam
    from app.models.notification import Notification
    from app.models.task import Task
    from app.models.whatsapp_message import WhatsappMessage

    now = datetime.datetime.utcnow()
    rows = [
        Task(
            title="owner-only task",
            source="test",
            user_id=owner_id,
        ),
        Event(
            title="owner-only event",
            start_time=now,
            end_time=now + datetime.timedelta(hours=1),
            source="test",
            user_id=owner_id,
        ),
        Assignment(
            title="owner-only assignment",
            source="test",
            user_id=owner_id,
        ),
        Exam(
            title="owner-only exam",
            exam_date=now + datetime.timedelta(days=3),
            user_id=owner_id,
        ),
        Email(
            message_id=f"test-msg-{now.timestamp()}",
            thread_id="thread-1",
            from_email="someone@example.com",
            from_name="Someone",
            to_email="owner@example.com",
            subject="owner-only email",
            body_text="hello",
            received_at=now,
            category="important",
            user_id=owner_id,
        ),
        Document(
            filename="owner-only.pdf",
            file_type="application/pdf",
            file_size=100,
            storage_path="/tmp/owner-only.pdf",
            is_indexed=False,
            user_id=owner_id,
        ),
        Notification(
            type="info",
            title="owner-only notification",
            message="hello",
            fingerprint=f"test-fp-{now.timestamp()}",
            user_id=owner_id,
        ),
        WhatsappMessage(
            direction="in",
            from_number="+10000000001",
            to_number="+10000000002",
            text="owner-only whatsapp",
            user_id=owner_id,
        ),
    ]
    for row in rows:
        db.add(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def test_no_token_is_401_across_endpoints(client, owner):
    for path in (
        "/api/tasks",
        "/api/events",
        "/api/assignments",
        "/api/exams",
        "/api/emails",
        "/api/documents",
        "/api/notifications",
        "/api/attendance",
        "/api/timetable",
        "/api/dashboard",
    ):
        r = client.get(path)
        assert r.status_code == 401, f"{path} -> {r.status_code}"


def test_member_cannot_read_owner_created_task(client, owner, member):
    created = client.post(
        "/api/tasks",
        json={"title": "A-private-task", "priority": "high", "status": "pending"},
        headers=auth(owner["token"]),
    )
    assert created.status_code in (200, 201)
    task_id = created.json()["id"]

    listed = client.get("/api/tasks", headers=auth(member["token"])).json()
    assert [t["id"] for t in listed] == []

    assert (
        client.get(f"/api/tasks/{task_id}", headers=auth(member["token"])).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/tasks/{task_id}",
            json={"title": "hijack"},
            headers=auth(member["token"]),
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/tasks/{task_id}", headers=auth(member["token"])).status_code
        == 404
    )

    owner_listed = client.get("/api/tasks", headers=auth(owner["token"])).json()
    assert task_id in [t["id"] for t in owner_listed]


def test_owner_cannot_see_member_rows(client, owner, member):
    client.post(
        "/api/tasks",
        json={"title": "member-task"},
        headers=auth(member["token"]),
    )
    owner_listed = client.get("/api/tasks", headers=auth(owner["token"])).json()
    assert [t["id"] for t in owner_listed] == []
    member_listed = client.get("/api/tasks", headers=auth(member["token"])).json()
    assert len(member_listed) == 1


def test_seeded_rows_isolated_everywhere(client, db, owner, member):
    rows = _seed_owner_rows(db, owner["id"], client)

    list_paths = {
        "Task": "/api/tasks",
        "Event": "/api/events",
        "Assignment": "/api/assignments",
        "Exam": "/api/exams",
        "Email": "/api/emails",
        "Document": "/api/documents",
        "Notification": "/api/notifications",
    }
    for kind, path in list_paths.items():
        member_rows = client.get(path, headers=auth(member["token"])).json()
        assert (
            member_rows == []
        ), f"member should see no {kind}, got {len(member_rows)}"
        owner_rows = client.get(path, headers=auth(owner["token"])).json()
        assert len(owner_rows) >= 1, f"owner should see their {kind}"


def test_member_cannot_read_owner_rows_by_id(client, db, owner, member):
    rows = _seed_owner_rows(db, owner["id"], client)
    # Only endpoints with a by-id GET route are tested here (Task, Event,
    # Email); the rest are covered by the list-isolation test above.
    by_id = {
        "Task": ("/api/tasks/{}", rows[0].id),
        "Event": ("/api/events/{}", rows[1].id),
        "Email": ("/api/emails/{}", rows[4].id),
    }
    for kind, (path, row_id) in by_id.items():
        r = client.get(path.format(row_id), headers=auth(member["token"]))
        assert r.status_code == 404, f"member read owner {kind} -> {r.status_code}"


def test_dashboard_is_scoped(client, db, owner, member):
    _seed_owner_rows(db, owner["id"], client)

    member_dash = client.get("/api/dashboard", headers=auth(member["token"])).json()
    assert member_dash["email_stats"]["total"] == 0
    assert member_dash["task_stats"]["total"] == 0
    assert member_dash["today_events"] == 0
    assert member_dash["documents_count"] == 0

    owner_dash = client.get("/api/dashboard", headers=auth(owner["token"])).json()
    assert owner_dash["email_stats"]["total"] == 1
    assert owner_dash["task_stats"]["total"] == 1