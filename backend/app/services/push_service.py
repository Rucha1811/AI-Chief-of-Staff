import base64
import logging
import os
import time
from datetime import timedelta

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.config import settings
from app.core.timeutil import utcnow
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.services.ownership import owner_user_id

logger = logging.getLogger(__name__)

_KEY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "vapid")
_PRIVATE_KEY_FILE = os.path.join(_KEY_DIR, "private.pem")
_PUBLIC_KEY_FILE = os.path.join(_KEY_DIR, "public.b64")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _valid_vapid_key(value: str, expected_len: int) -> bool:
    """A valid URL-safe base64 key that decodes to exactly the expected bytes."""
    if not value:
        return False
    try:
        pad = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + pad)
    except (ValueError, TypeError):
        return False
    return len(decoded) == expected_len


def _is_valid_subscription(sub) -> bool:
    """p256dh must be a 65-byte uncompressed point; auth must be 16 bytes."""
    endpoint = (sub.endpoint or "").strip()
    if not endpoint.startswith("https://"):
        return False
    return _valid_vapid_key(sub.p256dh or "", 65) and _valid_vapid_key(sub.auth or "", 16)


def _ensure_vapid_keys() -> str:
    """Generate and persist VAPID keys if missing. Returns the public key (urlsafe base64).

    The private key is a signing credential, so it is written 0600 and must not
    be readable by other users on the host.
    """
    if os.path.exists(_PUBLIC_KEY_FILE):
        with open(_PUBLIC_KEY_FILE) as f:
            return f.read().strip()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    os.makedirs(_KEY_DIR, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    # Write via a 0600 file descriptor so the private key is never briefly
    # world-readable between creation and chmod.
    fd = os.open(_PRIVATE_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
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


def save_subscription(sub: dict, user_id: int | None = None) -> bool:
    """Register (or refresh) a Web Push subscription for ``user_id``.

    ``endpoint`` stays globally unique - it is one browser registration, not a
    per-user value - so an existing row is updated in place rather than
    duplicated.

    Ownership is rebound to the currently authenticated user on every
    subscription. A browser handed from User A to User B keeps the SAME push
    endpoint; without the rebind the row would stay owned by A and B would
    silently receive nothing (while A kept receiving pushes meant for the
    browser B is now using).

    ``user_id`` must come from the authenticated request (see
    /api/notifications/subscribe). When it is missing the row is still
    refreshed, but ownership is NEVER taken from, or moved to, the owner
    fallback: an unauthenticated caller cannot rebind an endpoint.
    """
    endpoint = (sub.get("endpoint") or "").strip()
    if not endpoint:
        return False
    keys = sub.get("keys") or {}
    if not _valid_vapid_key(keys.get("p256dh", ""), 65) or not _valid_vapid_key(
        keys.get("auth", ""), 16
    ):
        logger.warning("rejecting push subscription with invalid keys")
        return False
    with SessionLocal() as session:
        existing = session.execute(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        ).scalar_one_or_none()
        if existing:
            existing.p256dh = keys.get("p256dh", "")
            existing.auth = keys.get("auth", "")
            existing.expires_at = None
            if user_id is not None and existing.user_id != user_id:
                # The browser that owns this endpoint is now signed in as a
                # different user; move delivery with the browser.
                logger.info(
                    "push subscription %s reassigned from user %s to user %s",
                    endpoint,
                    existing.user_id,
                    user_id,
                )
                existing.user_id = user_id
            session.commit()
            return True
        session.add(
            PushSubscription(
                endpoint=endpoint,
                p256dh=keys.get("p256dh", ""),
                auth=keys.get("auth", ""),
                expires_at=None,
                user_id=user_id or owner_user_id(session),
            )
        )
        session.commit()
        return True


def _drop_subscription(endpoint: str) -> None:
    with SessionLocal() as session:
        session.execute(
            PushSubscription.__table__.delete().where(
                PushSubscription.endpoint == endpoint
            )
        )
        session.commit()


def _send(sub: PushSubscription, title: str, body: str, entity_type, entity_id):
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush is not installed; web push disabled")
        return False
    if not _is_valid_subscription(sub):
        logger.warning("dropping push subscription with invalid keys/endpoint")
        _drop_subscription(sub.endpoint)
        return False
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=__import__("json").dumps(
                {"title": title, "body": body, "entity_type": entity_type, "entity_id": entity_id}
            ),
            vapid_private_key=os.path.join(_KEY_DIR, "private.pem"),
            vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIMS_EMAIL}"},
        )
        return True
    except WebPushException as e:
        if getattr(e, "response", None) is not None and e.response.status_code in (404, 410):
            _drop_subscription(sub.endpoint)
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

    # Notification.created_at is written by the database (func.now(), UTC).
    # A host-local datetime.now() window would miss every notification by the
    # host's offset; the cutoff must use the same UTC clock (D5).
    cutoff = utcnow() - timedelta(minutes=10)
    sent = 0
    with SessionLocal() as session:
        notifs = session.execute(
            select(Notification).where(
                Notification.is_read.is_(False),
                Notification.created_at >= cutoff,
                Notification.priority == "high",
                Notification.push_sent_at.is_(None),
            )
        ).scalars().all()
        for n in notifs:
            # A notification is only delivered to the subscriptions of its
            # owner: never cross user boundaries.
            subs = session.execute(
                select(PushSubscription).where(PushSubscription.user_id == n.user_id)
            ).scalars().all()
            delivered = False
            for sub in subs:
                try:
                    if _send(sub, n.title, n.message or "", n.entity_type, n.entity_id):
                        sent += 1
                        delivered = True
                except Exception:
                    logger.warning(
                        "push delivery failed for subscription %s (user %s)",
                        sub.endpoint,
                        sub.user_id,
                    )
            if delivered:
                n.push_sent_at = utcnow()
        session.commit()
    return sent


def push_now(title: str, body: str, entity_type=None, entity_id=None) -> int:
    sent = 0
    with SessionLocal() as session:
        subs = session.execute(select(PushSubscription)).scalars().all()
        for sub in subs:
            if _send(sub, title, body, entity_type, entity_id):
                sent += 1
    return sent
