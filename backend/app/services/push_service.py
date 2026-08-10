import base64
import logging
import os
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)

_KEY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "vapid")
_PRIVATE_KEY_FILE = os.path.join(_KEY_DIR, "private.pem")
_PUBLIC_KEY_FILE = os.path.join(_KEY_DIR, "public.b64")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _ensure_vapid_keys() -> str:
    """Generate and persist VAPID keys if missing. Returns the public key (urlsafe base64)."""
    if os.path.exists(_PUBLIC_KEY_FILE):
        with open(_PUBLIC_KEY_FILE) as f:
            return f.read().strip()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    os.makedirs(_KEY_DIR, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    with open(_PRIVATE_KEY_FILE, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    public_b64 = _b64url(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    )
    with open(_PUBLIC_KEY_FILE, "w") as f:
        f.write(public_b64)
    return public_b64


def get_vapid_public_key() -> str:
    return _ensure_vapid_keys()


def save_subscription(sub: dict) -> bool:
    endpoint = (sub.get("endpoint") or "").strip()
    if not endpoint:
        return False
    keys = sub.get("keys") or {}
    with SessionLocal() as session:
        existing = session.execute(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        ).scalar_one_or_none()
        if existing:
            existing.p256dh = keys.get("p256dh", "")
            existing.auth = keys.get("auth", "")
            existing.expires_at = None
            session.commit()
            return True
        session.add(
            PushSubscription(
                endpoint=endpoint,
                p256dh=keys.get("p256dh", ""),
                auth=keys.get("auth", ""),
                expires_at=None,
            )
        )
        session.commit()
        return True


def _send(sub: PushSubscription, title: str, body: str, entity_type, entity_id):
    try:
        from pywebpush import WebPushException, webpush

        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=__import__("json").dumps(
                {"title": title, "body": body, "entity_type": entity_type, "entity_id": entity_id}
            ),
            vapid_private_key=os.path.join(_KEY_DIR, "private.pem"),
            vapid_claims={"sub": "mailto:aichief@localhost"},
        )
        return True
    except WebPushException as e:
        if getattr(e, "response", None) is not None and e.response.status_code in (404, 410):
            with SessionLocal() as session:
                session.execute(
                    PushSubscription.__table__.delete().where(
                        PushSubscription.endpoint == sub.endpoint
                    )
                )
                session.commit()
            return False
        logger.warning("push failed: %s", e)
        return False
    except Exception as e:
        logger.warning("push error: %s", e)
        return False


_last_send_ts: float = 0.0
_SEND_MIN_INTERVAL = 10


def push_pending_notifications() -> int:
    """Send browser pushes for high-priority notifications created since the last send."""
    global _last_send_ts
    now = time.time()
    if now - _last_send_ts < _SEND_MIN_INTERVAL:
        return 0
    _last_send_ts = now

    cutoff = datetime.now() - timedelta(minutes=10)
    sent = 0
    with SessionLocal() as session:
        subs = session.execute(select(PushSubscription)).scalars().all()
        if not subs:
            return 0
        notifs = session.execute(
            select(Notification).where(
                Notification.is_read.is_(False),
                Notification.created_at >= cutoff,
                Notification.priority == "high",
            )
        ).scalars().all()
        for n in notifs:
            for sub in subs:
                if _send(sub, n.title, n.message or "", n.entity_type, n.entity_id):
                    sent += 1
    return sent


def push_now(title: str, body: str, entity_type=None, entity_id=None) -> int:
    sent = 0
    with SessionLocal() as session:
        subs = session.execute(select(PushSubscription)).scalars().all()
        for sub in subs:
            if _send(sub, title, body, entity_type, entity_id):
                sent += 1
    return sent
