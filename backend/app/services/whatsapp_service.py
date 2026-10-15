import base64
import hashlib
import hmac as hmac_mod
import logging
from typing import Optional

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.whatsapp_message import WhatsappMessage
from app.services.ownership import owner_user_id

logger = logging.getLogger(__name__)

_TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
_META_URL = "https://graph.facebook.com/v19.0/{phone_number_id}/messages"


def verify_twilio_signature(
    auth_token: str, url: str, params: dict, signature: Optional[str]
) -> bool:
    """Validate a Twilio webhook signature (X-Twilio-Signature).

    Twilio signs ``url + ''.join(KEY + VALUE for each param sorted by key)``
    with HMAC-SHA1 using the account auth token. Comparison is constant-time.
    """
    if not auth_token or not signature:
        return False
    payload = url
    for key, value in sorted(params.items()):
        payload += f"{key}{value}"
    expected = base64.b64encode(
        hmac_mod.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac_mod.compare_digest(expected, signature)


def verify_meta_signature(
    app_secret: str, raw_body: bytes, header: Optional[str]
) -> bool:
    """Validate a Meta WhatsApp Cloud API signature (X-Hub-Signature-256).

    Meta signs the raw request body with HMAC-SHA256 using the app secret and
    sends ``sha256=<hexdigest>``. Comparison is constant-time.
    """
    if not app_secret or not header:
        return False
    if not header.startswith("sha256="):
        return False
    provided = header[len("sha256="):]
    expected = hmac_mod.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac_mod.compare_digest(expected, provided)


def _save(db: Session, *, message_id: str, direction: str,
          from_number: str, to_number: str, text: str, provider: str,
          user_id: Optional[int] = None) -> WhatsappMessage:
    owner = user_id or owner_user_id(db)
    # Provider message ids (Twilio SID / Meta msg id) are globally unique by
    # provider spec and DB-enforced. Provider webhooks redeliver on timeouts,
    # so a repeated id for the SAME owner is the same message: return the
    # existing row instead of crashing into the unique constraint. An id that
    # already belongs to a DIFFERENT user is a real uniqueness violation and is
    # allowed to raise (never silently re-attribute another user's row).
    if message_id:
        existing = (
            db.query(WhatsappMessage)
            .filter(
                WhatsappMessage.message_id == message_id,
                WhatsappMessage.user_id == owner,
            )
            .first()
        )
        if existing is not None:
            return existing
    row = WhatsappMessage(
        message_id=message_id or None,
        direction=direction,
        from_number=from_number,
        to_number=to_number,
        text=text,
        provider=provider,
        user_id=owner,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # The global unique index rejected this id because another user's row
        # already owns it. Roll back so the session leaves the failed INSERT in
        # a usable state (an aborted transaction poisons every later statement
        # on the same session), then propagate. The existing row is never
        # updated, deleted, or re-attributed.
        db.rollback()
        logger.warning(
            "whatsapp message_id %r already owned by another user; "
            "leaving the existing row untouched",
            message_id,
        )
        raise
    db.refresh(row)
    return row


def _send_twilio(to: str, body: str) -> Optional[str]:
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    if not sid or not token:
        return None
    resp = requests.post(
        _TWILIO_URL.format(sid=sid),
        auth=(sid, token),
        data={
            "From": settings.TWILIO_WHATSAPP_FROM,
            "To": _wa(to),
            "Body": body[:1600],
        },
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error("twilio send failed: %s %s", resp.status_code, resp.text[:500])
        return None
    return resp.json().get("sid")


def _send_meta(to: str, body: str) -> Optional[str]:
    phone_id = settings.META_WA_PHONE_NUMBER_ID
    token = settings.META_WA_ACCESS_TOKEN
    if not phone_id or not token:
        return None
    resp = requests.post(
        _META_URL.format(phone_number_id=phone_id),
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to.replace("+", ""),
            "type": "text",
            "text": {"body": body[:4096]},
        },
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error("meta send failed: %s %s", resp.status_code, resp.text[:500])
        return None
    return resp.json().get("messages", [{}])[0].get("id")


def _wa(number: str) -> str:
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def send_whatsapp(db: Session, to: Optional[str], body: str,
                  user_id: Optional[int] = None) -> dict:
    """Send an outbound WhatsApp message through the configured provider."""
    provider = (settings.WHATSAPP_PROVIDER or "none").lower()

    if not user_id:
        user_id = owner_user_id(db)

    from app.models.user_integration import UserIntegration
    integration = db.query(UserIntegration).filter(
        UserIntegration.user_id == user_id,
        UserIntegration.provider == "whatsapp",
        UserIntegration.status == "active"
    ).first()

    to = to or (integration.account_id if integration else None)
    if not to or not body:
        return {"ok": False, "error": "Missing recipient or body", "skipped": True}

    if provider in ("twilio", "twilio_sandbox"):
        message_id = _send_twilio(_wa(to), body)
        if not message_id:
            return {"ok": False, "provider": provider, "error": "Twilio send failed"}
        _save(db, message_id=message_id, direction="out",
              from_number=settings.TWILIO_WHATSAPP_FROM, to_number=_wa(to),
              text=body, provider=provider, user_id=user_id)
        return {"ok": True, "provider": provider, "message_id": message_id}

    if provider in ("meta", "whatsapp_cloud_api"):
        message_id = _send_meta(to, body)
        if not message_id:
            return {"ok": False, "provider": provider, "error": "Meta send failed"}
        _save(db, message_id=message_id, direction="out",
              from_number=f"meta:{settings.META_WA_PHONE_NUMBER_ID or ''}",
              to_number=to, text=body, provider=provider, user_id=user_id)
        return {"ok": True, "provider": provider, "message_id": message_id}

    return {"ok": False, "provider": "none",
            "error": "WHATSAPP_PROVIDER not configured (set to twilio or meta)",
            "skipped": True}


def save_inbound(db: Session, *, message_id: Optional[str], from_number: str,
                 to_number: str, text: str, provider: str) -> WhatsappMessage:
    from app.models.user_integration import UserIntegration

    clean_from = from_number.replace("whatsapp:", "")
    # Two accounts may point at the same WhatsApp number; order by the stable
    # primary key so the resolved owner is deterministic instead of whatever
    # order the planner happens to return.
    integration = (
        db.query(UserIntegration)
        .filter(
            UserIntegration.provider == "whatsapp",
            UserIntegration.account_id == clean_from,
        )
        .order_by(UserIntegration.id.asc())
        .first()
    )

    user_id = integration.user_id if integration else None

    return _save(db, message_id=message_id, direction="in",
                 from_number=from_number, to_number=to_number,
                 text=text, provider=provider, user_id=user_id)


def reply_to_inbound(db: Session, *, from_number: str, to_number: str,
                     body: str, provider: str) -> dict:
    """Echo-style reply for inbound WhatsApp messages (pre-AI wiring)."""
    reply = f"AI Chief received your message: \"{body}\""
    return send_whatsapp(db, to=from_number, body=reply)