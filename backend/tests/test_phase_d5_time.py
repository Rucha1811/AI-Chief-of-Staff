"""Phase D5 - UTC timestamp regression tests.

The database records ``created_at`` with ``func.now()``, which is PostgreSQL's
clock (UTC in every container in this project). Push delivery windows, reminder
generation windows and the read-notification retention purge previously used a
host-local ``datetime.now()``, so on a host not running UTC every window was
skewed by the offset: notifications stamped "now" in UTC looked minutes or
hours old and were never pushed, and reminders for UTC-stamped entity times
fell outside the host-local window.

These tests pin the corrected behaviour (app.core.timeutil.utcnow):

  1. recent notifications are detected correctly,
  2. old notifications are excluded,
  3. different users remain isolated,
  4. global uniqueness remains correct (per-user fingerprint scoping),
  5. per-user synchronization remains correct (reminders owned by the entity's
     user, windows measured in UTC).
"""

import datetime

from app.models.event import Event
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.task import Task
from app.services import push_service
from app.services.reminder_service import generate_reminders


def _subscribe(db, user_id, endpoint):
    db.add(
        PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh="p256dh-key",
            auth="auth-secret",
        )
    )


def _notify(db, user_id, title, fingerprint, created_at):
    db.add(
        Notification(
            user_id=user_id,
            type="info",
            title=title,
            message=f"{title} body",
            priority="high",
            fingerprint=fingerprint,
            created_at=created_at,
        )
    )


def _run_push(monkeypatch, sent):
    def _fake_send(sub, title, body, entity_type, entity_id):
        sent.append((sub.user_id, sub.endpoint, title))
        return True

    monkeypatch.setattr(push_service, "_send", _fake_send)
    push_service._last_send_ts = 0.0
    return push_service.push_pending_notifications()


# ---------------------------------------------------------------------------
# 1 + 2. The 10-minute push window is measured in UTC, like created_at.
# ---------------------------------------------------------------------------


def test_push_delivers_notifications_created_in_utc_window(db, owner, monkeypatch):
    """A notification created minutes ago (UTC) is inside the window."""
    now = datetime.datetime.utcnow()
    _subscribe(db, owner["id"], "https://push/utc-recent")
    _notify(db, owner["id"], "Recent alert", "fp-utc-recent", now - datetime.timedelta(minutes=5))
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert [(u, t) for u, e, t in sent if u == owner["id"]] == [
        (owner["id"], "Recent alert")
    ]


def test_push_excludes_notifications_older_than_window(db, owner, monkeypatch):
    """A notification created 20 minutes ago (UTC) is outside the window."""
    now = datetime.datetime.utcnow()
    _subscribe(db, owner["id"], "https://push/utc-old")
    _notify(db, owner["id"], "Old alert", "fp-utc-old", now - datetime.timedelta(minutes=20))
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert sent == []


def test_push_window_boundary_just_inside(db, owner, monkeypatch):
    """Nine minutes (UTC) is still inside the window; the boundary is 10m."""
    now = datetime.datetime.utcnow()
    _subscribe(db, owner["id"], "https://push/utc-edge")
    _notify(db, owner["id"], "Edge alert", "fp-utc-edge", now - datetime.timedelta(minutes=9))
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert [(u, t) for u, e, t in sent if u == owner["id"]] == [
        (owner["id"], "Edge alert")
    ]


# ---------------------------------------------------------------------------
# 3. Multi-user isolation holds under UTC timestamps.
# ---------------------------------------------------------------------------


def test_push_isolation_holds_with_utc_timestamps(db, owner, member, monkeypatch):
    """A's recent notification pushes to A only; B's older one does not leak."""
    now = datetime.datetime.utcnow()
    _subscribe(db, owner["id"], "https://push/iso-a")
    _subscribe(db, member["id"], "https://push/iso-b")
    _notify(db, owner["id"], "A recent", "fp-iso-a", now - datetime.timedelta(minutes=2))
    _notify(db, member["id"], "B old", "fp-iso-b", now - datetime.timedelta(minutes=20))
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert [(u, t) for u, e, t in sent] == [(owner["id"], "A recent")]
    assert all(u == owner["id"] for u, e, t in sent)


# ---------------------------------------------------------------------------
# 4. Global uniqueness stays correct: per-user fingerprint scoping still
#    delivers to BOTH users when both are recent and isolated.
# ---------------------------------------------------------------------------


def test_fingerprint_scoped_push_still_reaches_both_users(db, owner, member, monkeypatch):
    now = datetime.datetime.utcnow()
    _subscribe(db, owner["id"], "https://push/uniq-a")
    _subscribe(db, member["id"], "https://push/uniq-b")
    # Same fingerprint for two users - legal under ux_notifications_user_fingerprint.
    _notify(db, owner["id"], "Shared A", "fp:event:42:2026-10-06", now - datetime.timedelta(minutes=1))
    _notify(db, member["id"], "Shared B", "fp:event:42:2026-10-06", now - datetime.timedelta(minutes=1))
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert sorted((u, t) for u, e, t in sent) == [
        (owner["id"], "Shared A"),
        (member["id"], "Shared B"),
    ]


# ---------------------------------------------------------------------------
# 5. Per-user synchronization: reminder windows & purge use UTC, and each
#    reminder stays owned by the entity's user.
# ---------------------------------------------------------------------------


def test_reminder_windows_use_utc_for_events(db, owner, member):
    """An event starting in 2h gets a reminder; one starting 2h ago does not."""
    now = datetime.datetime.utcnow()
    db.add(
        Event(
            user_id=owner["id"],
            title="Upcoming",
            start_time=now + datetime.timedelta(hours=2),
            end_time=now + datetime.timedelta(hours=3),
            source="test",
            is_all_day=False,
        )
    )
    db.add(
        Event(
            user_id=member["id"],
            title="Missed window",
            start_time=now - datetime.timedelta(hours=2),
            end_time=now - datetime.timedelta(hours=1),
            source="test",
            is_all_day=False,
        )
    )
    db.commit()

    created = generate_reminders(force=True)
    assert created == 1

    rows = db.query(Notification).filter(Notification.type == "event").all()
    assert len(rows) == 1
    assert rows[0].title.startswith("Upcoming")
    assert rows[0].user_id == owner["id"]


def test_reminder_windows_use_utc_for_tasks(db, owner, member):
    """A task due in 20h triggers; one due in 60h (outside 48h) does not."""
    now = datetime.datetime.utcnow()
    db.add(
        Task(
            user_id=owner["id"],
            title="Due soon",
            due_date=now + datetime.timedelta(hours=20),
            source="test",
            status="pending",
        )
    )
    db.add(
        Task(
            user_id=member["id"],
            title="Too far out",
            due_date=now + datetime.timedelta(hours=60),
            source="test",
            status="pending",
        )
    )
    db.commit()

    created = generate_reminders(force=True)
    assert created == 1

    rows = db.query(Notification).filter(Notification.type == "task").all()
    assert len(rows) == 1
    assert rows[0].title == "Due soon: Due soon"
    assert rows[0].user_id == owner["id"]


def test_reminder_retention_purge_uses_utc_cutoff(db, owner):
    """Read notifications older than 14 days (UTC) are purged; recent stay."""
    now = datetime.datetime.utcnow()

    def _read_notification(created_at):
        db.add(
            Notification(
                user_id=owner["id"],
                type="info",
                title="done",
                message="done",
                priority="normal",
                fingerprint=f"fp-purge-{created_at.timestamp()}",
                is_read=True,
                created_at=created_at,
            )
        )

    _read_notification(now - datetime.timedelta(days=15))
    _read_notification(now - datetime.timedelta(days=1))
    db.commit()

    generate_reminders(force=True)
    db.expire_all()

    remaining = db.query(Notification).filter(Notification.is_read.is_(True)).all()
    assert len(remaining) == 1
    assert remaining[0].created_at >= now - datetime.timedelta(days=14)