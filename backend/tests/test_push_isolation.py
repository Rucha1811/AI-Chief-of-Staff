"""P0-2: Push notification delivery isolation.

A notification belonging to User A must only be delivered to User A's push
subscriptions. User B must never receive A's notifications (and vice versa).
Multiple subscriptions for the same user must each receive that user's alerts.
"""

import datetime

from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.services import push_service


def _subscribe(db, user_id, endpoint):
    db.add(
        PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh="p256dh-key",
            auth="auth-secret",
        )
    )


def _notify(db, user_id, title, fingerprint):
    db.add(
        Notification(
            user_id=user_id,
            type="info",
            title=title,
            message=f"{title} body",
            priority="high",
            fingerprint=fingerprint,
        )
    )


def _run_push(monkeypatch, sent):
    def _fake_send(sub, title, body, entity_type, entity_id):
        sent.append((sub.user_id, sub.endpoint, title))
        return True

    monkeypatch.setattr(push_service, "_send", _fake_send)
    push_service._last_send_ts = 0.0
    return push_service.push_pending_notifications()


def test_push_delivery_isolated_per_user(db, owner, member, monkeypatch):
    # A has two subscriptions; B has one.
    _subscribe(db, owner["id"], "https://push/owner-1")
    _subscribe(db, owner["id"], "https://push/owner-2")
    _subscribe(db, member["id"], "https://push/member-1")
    _notify(db, owner["id"], "A alert", "fp-a-1")
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    owner_subs = sorted(e for u, e, t in sent if u == owner["id"])
    assert owner_subs == ["https://push/owner-1", "https://push/owner-2"]
    assert [t for u, e, t in sent if u == owner["id"]] == ["A alert", "A alert"]
    assert [t for u, e, t in sent if u == member["id"]] == []

    # Now B gets a notification while A's is already consumed: B receives it,
    # A receives nothing.
    for row in db.query(Notification).filter(Notification.user_id == owner["id"]):
        row.is_read = True
    _notify(db, member["id"], "B alert", "fp-b-1")
    db.commit()

    sent.clear()
    _run_push(monkeypatch, sent)

    assert [t for u, e, t in sent if u == member["id"]] == ["B alert"]
    assert [t for u, e, t in sent if u == owner["id"]] == []


def test_push_never_cross_streams_when_both_have_notifications(db, owner, member, monkeypatch):
    _subscribe(db, owner["id"], "https://push/owner-1")
    _subscribe(db, member["id"], "https://push/member-1")
    _notify(db, owner["id"], "A alert", "fp-a-both")
    _notify(db, member["id"], "B alert", "fp-b-both")
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert [t for u, e, t in sent if u == owner["id"]] == ["A alert"]
    assert [t for u, e, t in sent if u == member["id"]] == ["B alert"]


def test_push_failure_for_one_user_does_not_block_others(db, owner, member, monkeypatch):
    _subscribe(db, owner["id"], "https://push/owner-bad")
    _subscribe(db, owner["id"], "https://push/owner-good")
    _subscribe(db, member["id"], "https://push/member-1")
    _notify(db, owner["id"], "A alert", "fp-a-fail")
    _notify(db, member["id"], "B alert", "fp-b-fail")
    db.commit()

    sent = []

    def _flaky(sub, title, body, entity_type, entity_id):
        sent.append((sub.user_id, sub.endpoint, title))
        if sub.endpoint == "https://push/owner-bad":
            raise RuntimeError("push service error")
        return True

    monkeypatch.setattr(push_service, "_send", _flaky)
    push_service._last_send_ts = 0.0
    push_service.push_pending_notifications()

    # The failing A subscription did not stop A's other subscription or B's delivery.
    assert any(e == "https://push/owner-good" for u, e, t in sent)
    assert any(e == "https://push/owner-bad" for u, e, t in sent)
    assert [t for u, e, t in sent if u == member["id"]] == ["B alert"]


def test_only_high_priority_notifications_are_pushed(db, owner, member, monkeypatch):
    _subscribe(db, owner["id"], "https://push/owner-1")
    _notify(db, owner["id"], "A high", "fp-high")
    db.add(
        Notification(
            user_id=member["id"],
            type="info",
            title="B low",
            message="low",
            priority="low",
            fingerprint="fp-low",
        )
    )
    db.commit()

    sent = []
    _run_push(monkeypatch, sent)

    assert [t for u, e, t in sent] == ["A high"]
    assert all(u == owner["id"] for u, e, t in sent)