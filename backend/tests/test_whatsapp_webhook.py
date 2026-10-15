"""P0-3: WhatsApp webhook authenticity.

The public webhook must reject unauthenticated requests (invalid/missing
signature) before any message is processed, accept valid provider requests
(Twilio X-Twilio-Signature; Meta X-Hub-Signature-256), require a configured
verification token for the GET handshake, and keep From -> user routing intact
so verification never causes cross-user routing.
"""

import base64
import hashlib
import hmac
import json

import pytest

from app.core.config import settings
from tests.conftest import auth

_TWILIO_WEBHOOK_URL = "http://testserver/api/webhooks/whatsapp"


def _meta_sig(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _twilio_sig(token: str, url: str, params: dict) -> str:
    payload = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()


@pytest.fixture
def _meta(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "meta")
    monkeypatch.setattr(settings, "META_WA_APP_SECRET", "test-meta-app-secret")
    yield


@pytest.fixture
def _twilio(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "test-twilio-auth-token")
    yield


META_MSG = {
    "entry": [
        {
            "changes": [
                {
                    "value": {
                        "messages": [
                            {
                                "id": "wamid.MOCK",
                                "from": "15550000001",
                                "type": "text",
                                "text": {"body": "hello from webhook"},
                            }
                        ]
                    }
                }
            ]
        }
    ]
}


# --------------------------------------------------------------------------- #
# Provider / configuration gating
# --------------------------------------------------------------------------- #
def test_webhook_rejected_when_provider_not_configured(client):
    r = client.post("/api/webhooks/whatsapp", json=META_MSG)
    assert r.status_code == 503


# --------------------------------------------------------------------------- #
# Meta WhatsApp Cloud API
# --------------------------------------------------------------------------- #
def test_meta_valid_signature_accepted(client, db, owner, _meta):
    raw = json.dumps(META_MSG).encode()
    r = client.post(
        "/api/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _meta_sig(settings.META_WA_APP_SECRET, raw),
        },
    )
    assert r.status_code == 200
    assert r.json()["received"] is True

    from app.models.whatsapp_message import WhatsappMessage

    rows = db.query(WhatsappMessage).all()
    assert len(rows) == 1
    assert rows[0].direction == "in"
    assert rows[0].text == "hello from webhook"
    # Unknown sender falls back to the owner (established behavior, preserved).
    assert rows[0].user_id == owner["id"]


def test_meta_invalid_signature_rejected(client, db, owner, _meta):
    raw = json.dumps(META_MSG).encode()
    r = client.post(
        "/api/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _meta_sig(settings.META_WA_APP_SECRET, b"tampered"),
        },
    )
    assert r.status_code == 401

    from app.models.whatsapp_message import WhatsappMessage

    assert db.query(WhatsappMessage).count() == 0


def test_meta_missing_signature_rejected(client, db, owner, _meta):
    r = client.post(
        "/api/webhooks/whatsapp",
        content=json.dumps(META_MSG).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401

    from app.models.whatsapp_message import WhatsappMessage

    assert db.query(WhatsappMessage).count() == 0


def test_meta_known_sender_routes_to_their_user(client, db, owner, member, _meta):
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=member["id"],
            provider="whatsapp",
            account_id="15550000002",
            status="active",
        )
    )
    db.commit()

    msg_a = {"entry": [{"changes": [{"value": {"messages": [{
        "id": "wamid.MEMBER",
        "from": "15550000002",
        "type": "text",
        "text": {"body": "member msg"},
    }]}}]}]}
    raw_a = json.dumps(msg_a).encode()
    r_a = client.post(
        "/api/webhooks/whatsapp",
        content=raw_a,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _meta_sig(settings.META_WA_APP_SECRET, raw_a),
        },
    )
    assert r_a.status_code == 200

    msg_b = {"entry": [{"changes": [{"value": {"messages": [{
        "id": "wamid.UNKNOWN",
        "from": "15550000099",
        "type": "text",
        "text": {"body": "unknown sender"},
    }]}}]}]}
    raw_b = json.dumps(msg_b).encode()
    r_b = client.post(
        "/api/webhooks/whatsapp",
        content=raw_b,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _meta_sig(settings.META_WA_APP_SECRET, raw_b),
        },
    )
    assert r_b.status_code == 200

    from app.models.whatsapp_message import WhatsappMessage

    rows = db.query(WhatsappMessage).all()
    by_id = {r.message_id: r.user_id for r in rows}
    # Authenticated traffic is still routed purely by From -> user mapping.
    assert by_id["wamid.MEMBER"] == member["id"]
    assert by_id["wamid.UNKNOWN"] == owner["id"]


# --------------------------------------------------------------------------- #
# Twilio
# --------------------------------------------------------------------------- #
def test_twilio_valid_signature_accepted(client, db, owner, _twilio):
    params = {
        "MessageSid": "SM123",
        "AccountSid": "AC123",
        "ApiVersion": "2010-04-01",
        "From": "whatsapp:+15550000001",
        "To": "whatsapp:+14155238886",
        "Body": "twilio hello",
    }
    sig = _twilio_sig(settings.TWILIO_AUTH_TOKEN, _TWILIO_WEBHOOK_URL, params)
    r = client.post("/api/webhooks/whatsapp", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    assert r.json()["received"] is True

    from app.models.whatsapp_message import WhatsappMessage

    rows = db.query(WhatsappMessage).all()
    assert len(rows) == 1
    assert rows[0].direction == "in"
    assert rows[0].text == "twilio hello"
    # Unknown Twilio sender (From not linked to any user) falls back to owner.
    assert rows[0].user_id == owner["id"]


def test_twilio_invalid_signature_rejected(client, db, _twilio):
    params = {"MessageSid": "SM123", "From": "whatsapp:+15550000001", "Body": "hi"}
    r = client.post(
        "/api/webhooks/whatsapp",
        data=params,
        headers={"X-Twilio-Signature": "not-a-valid-signature=="},
    )
    assert r.status_code == 401

    from app.models.whatsapp_message import WhatsappMessage

    assert db.query(WhatsappMessage).count() == 0


def test_twilio_missing_signature_rejected(client, db, _twilio):
    params = {"MessageSid": "SM123", "From": "whatsapp:+15550000001", "Body": "hi"}
    r = client.post("/api/webhooks/whatsapp", data=params)
    assert r.status_code == 401

    from app.models.whatsapp_message import WhatsappMessage

    assert db.query(WhatsappMessage).count() == 0


def test_twilio_known_sender_routes_to_their_user(client, db, owner, member, _twilio):
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=member["id"],
            provider="whatsapp",
            account_id="+15550000002",
            status="active",
        )
    )
    db.commit()

    params = {
        "MessageSid": "SM555",
        "From": "whatsapp:+15550000002",
        "To": "whatsapp:+14155238886",
        "Body": "member via twilio",
    }
    sig = _twilio_sig(settings.TWILIO_AUTH_TOKEN, _TWILIO_WEBHOOK_URL, params)
    r = client.post("/api/webhooks/whatsapp", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200

    from app.models.whatsapp_message import WhatsappMessage

    rows = db.query(WhatsappMessage).all()
    assert len(rows) == 1
    assert rows[0].user_id == member["id"]


# --------------------------------------------------------------------------- #
# GET webhook verification
# --------------------------------------------------------------------------- #
def test_verify_requires_configured_token(client, monkeypatch):
    monkeypatch.setattr(settings, "META_WA_VERIFY_TOKEN", "")
    r = client.get(
        "/api/webhooks/whatsapp",
        params={
            "hub_mode": "subscribe",
            "hub_verify_token": "aichief",
            "hub_challenge": "123",
        },
    )
    assert r.status_code == 403


def test_verify_invalid_token_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "META_WA_VERIFY_TOKEN", "a-strong-verify-token")
    r = client.get(
        "/api/webhooks/whatsapp",
        params={
            "hub_mode": "subscribe",
            "hub_verify_token": "wrong",
            "hub_challenge": "123",
        },
    )
    assert r.status_code == 403


def test_verify_valid_token_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "META_WA_VERIFY_TOKEN", "a-strong-verify-token")
    r = client.get(
        "/api/webhooks/whatsapp",
        params={
            "hub_mode": "subscribe",
            "hub_verify_token": "a-strong-verify-token",
            "hub_challenge": "123456",
        },
    )
    assert r.status_code == 200
    assert r.json() == 123456


# --------------------------------------------------------------------------- #
# Verification primitives
# --------------------------------------------------------------------------- #
def test_verify_twilio_signature_rejects_bad_token():
    from app.services.whatsapp_service import verify_twilio_signature

    params = {"From": "whatsapp:+1", "Body": "hi"}
    good = _twilio_sig("token", "http://x/wa", params)
    assert verify_twilio_signature("token", "http://x/wa", params, good) is True
    assert verify_twilio_signature("other", "http://x/wa", params, good) is False
    assert verify_twilio_signature("token", "http://x/wa", params, None) is False


def test_verify_meta_signature_rejects_bad_secret():
    from app.services.whatsapp_service import verify_meta_signature

    raw = b'{"entry": []}'
    good = _meta_sig("appsecret", raw)
    assert verify_meta_signature("appsecret", raw, good) is True
    assert verify_meta_signature("wrong", raw, good) is False
    assert verify_meta_signature("appsecret", raw, "nonsense") is False
    assert verify_meta_signature("appsecret", raw, None) is False