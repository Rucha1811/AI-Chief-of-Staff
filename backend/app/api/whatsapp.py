import json
import hmac
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.limits import clamp_limit
from app.models.user import User
from app.services import whatsapp_service
from app.api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["whatsapp"])

_PROVIDERS_TWILIO = ("twilio", "twilio_sandbox")
_PROVIDERS_META = ("meta", "whatsapp_cloud_api")


class WhatsAppSendRequest(BaseModel):
    to: str = ""
    body: str


@router.post("/whatsapp/send")
async def whatsapp_send(
    req: WhatsAppSendRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return whatsapp_service.send_whatsapp(db, to=req.to, body=req.body, user_id=user.id)


class WhatsAppConnectRequest(BaseModel):
    phone_number: str

@router.post("/whatsapp/connect")
async def whatsapp_connect(
    req: WhatsAppConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.user_integration import UserIntegration
    integration = db.query(UserIntegration).filter(
        UserIntegration.user_id == user.id,
        UserIntegration.provider == "whatsapp"
    ).first()

    clean_phone = req.phone_number.replace("whatsapp:", "").strip("+").strip()
    clean_phone = f"+{clean_phone}"

    if integration:
        integration.account_id = clean_phone
        integration.status = "active"
    else:
        integration = UserIntegration(
            user_id=user.id,
            provider="whatsapp",
            account_id=clean_phone,
            status="active"
        )
        db.add(integration)

    db.commit()
    return {"ok": True, "phone_number": clean_phone}

@router.post("/whatsapp/disconnect")
async def whatsapp_disconnect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.user_integration import UserIntegration
    db.query(UserIntegration).filter(
        UserIntegration.user_id == user.id,
        UserIntegration.provider == "whatsapp"
    ).delete()
    db.commit()
    return {"ok": True}


@router.get("/whatsapp/messages")
async def whatsapp_messages(
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import select

    limit = clamp_limit(limit)
    rows = db.execute(
        select(whatsapp_service.WhatsappMessage)
        .where(whatsapp_service.WhatsappMessage.user_id == user.id)
        .order_by(whatsapp_service.WhatsappMessage.id.desc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "direction": r.direction,
            "from_number": r.from_number,
            "to_number": r.to_number,
            "text": r.text,
            "provider": r.provider,
        }
        for r in rows
    ]


def _require_provider():
    """Return the configured provider or 503 when none is configured."""
    provider = (settings.WHATSAPP_PROVIDER or "none").lower()
    if provider not in _PROVIDERS_TWILIO and provider not in _PROVIDERS_META:
        raise HTTPException(status_code=503, detail="WhatsApp provider not configured")
    return provider


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    provider = _require_provider()

    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")

    if provider in _PROVIDERS_TWILIO:
        # Twilio signs the request URL plus every form parameter (sorted by key)
        # with HMAC-SHA1 + the account auth token (X-Twilio-Signature).
        if "application/json" in content_type:
            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid JSON body")
        else:
            form_values = parse_qsl(raw_body.decode("utf-8", errors="replace"))
            payload = {k: v for k, v in form_values}
        params = {str(k): str(v) for k, v in payload.items()}
        signature = request.headers.get("X-Twilio-Signature", "")
        if not whatsapp_service.verify_twilio_signature(
            settings.TWILIO_AUTH_TOKEN or "",
            str(request.url),
            params,
            signature,
        ):
            raise HTTPException(status_code=401, detail="Invalid Twilio signature")
    else:  # Meta WhatsApp Cloud API signs the raw request body (X-Hub-Signature-256).
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not whatsapp_service.verify_meta_signature(
            settings.META_WA_APP_SECRET or "",
            raw_body,
            signature,
        ):
            raise HTTPException(status_code=401, detail="Invalid signature")

    if provider in _PROVIDERS_TWILIO:
        msg_id = payload.get("MessageSid") or payload.get("SmsMessageSid")
        from_number = payload.get("From", "")
        to_number = payload.get("To", "")
        text = payload.get("Body", "")
        if from_number:
            whatsapp_service.save_inbound(
                db, message_id=str(msg_id) if msg_id else None,
                from_number=str(from_number), to_number=str(to_number),
                text=str(text), provider=provider,
            )
            if provider in ("twilio", "twilio_sandbox"):
                whatsapp_service.reply_to_inbound(
                    db, from_number=str(from_number), to_number=str(to_number),
                    body=str(text), provider=provider,
                )
        return {"received": True, "provider": provider}

    # Meta branch: JSON body, either the generic {From/Body} shape or the
    # Cloud API {entry:[{changes:[{value:{messages:[...]}}]}]} envelope.
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if payload.get("From") or payload.get("Body") is not None:
        msg_id = payload.get("MessageSid") or payload.get("SmsMessageSid")
        from_number = payload.get("From", "")
        to_number = payload.get("To", "")
        text = payload.get("Body", "")
        if from_number:
            whatsapp_service.save_inbound(
                db, message_id=str(msg_id) if msg_id else None,
                from_number=str(from_number), to_number=str(to_number),
                text=str(text), provider=provider,
            )
        return {"received": True, "provider": provider}

    messages = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {})
            messages += value.get("messages", []) or []
    if messages:
        msg = messages[0]
        wa_id = msg.get("from", "")
        text = ""
        if msg.get("type") == "text":
            text = msg.get("text", {}).get("body", "")
        to_number = f"meta:{settings.META_WA_PHONE_NUMBER_ID or ''}"
        whatsapp_service.save_inbound(
            db, message_id=msg.get("id"), from_number=f"{wa_id}",
            to_number=to_number, text=text, provider="meta",
        )
        if provider in ("meta", "whatsapp_cloud_api"):
            whatsapp_service.reply_to_inbound(
                db, from_number=wa_id, to_number=settings.META_WA_PHONE_NUMBER_ID or "",
                body=text, provider="meta",
            )
        return {"received": True, "provider": "meta"}
    return {"received": True, "provider": provider}


@router.get("/webhooks/whatsapp")
async def whatsapp_webhook_verify(
    hub_mode: str = "", hub_verify_token: str = "", hub_challenge: str = "",
):
    expected = settings.META_WA_VERIFY_TOKEN or ""
    if hub_mode == "subscribe" and expected and hmac.compare_digest(
        hub_verify_token, expected
    ):
        try:
            return int(hub_challenge)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid challenge")
    raise HTTPException(status_code=403, detail="Verification failed")