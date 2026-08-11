import asyncio
import base64
import datetime
import html as html_lib
import http.client
import json
import os
import pickle
import random
import re
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Optional

import httplib2
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.models.email import Email
from app.core.database import SessionLocal
from app.services.google_auth import (
    SCOPES,
    CREDENTIALS_DIR,
    CREDENTIALS_FILE,
    TOKEN_FILE,
    GoogleAuthError,
    build_service,
    ensure_refreshed,
    load_credentials as _load_credentials,
    reauthorize as _google_reauthorize,
)
from app.services.auth_state import AuthStateStore

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
BATCH_SIZE = 50

DEFAULT_EMAIL_RETENTION_DAYS = 90
DEFAULT_EMAIL_SENSITIVITY = "PERSONAL"


class GmailAuthError(RuntimeError):
    pass


class GmailUnavailableError(RuntimeError):
    pass


async def _call_with_retry(request_builder, max_retries=MAX_RETRIES):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            request = request_builder()
            return await asyncio.to_thread(request.execute)
        except HttpError as e:
            status = e.resp.status
            if status in RETRYABLE_STATUS and attempt < max_retries:
                retry_after = e.resp.get("retry-after")
                delay = float(retry_after) if retry_after else 2**attempt + random.uniform(0, 1)
                last_error = e
                await asyncio.sleep(min(delay, 30))
                continue
            if status in (401, 403):
                AuthStateStore.mark_dead(
                    reason="revoked",
                    last_error=f"Gmail API rejected the token (HTTP {status}). "
                    "Re-authorize via /api/emails/re-auth.",
                )
                raise GmailAuthError("Gmail authorization is invalid or expired")
            raise GmailUnavailableError(f"Gmail API error (HTTP {status})") from e
        except (ConnectionError, TimeoutError, OSError, http.client.HTTPException, httplib2.HttpLib2Error, AttributeError) as e:
            if attempt < max_retries:
                delay = 2**attempt + random.uniform(0, 1)
                last_error = e
                await asyncio.sleep(min(delay, 30))
                continue
            raise GmailUnavailableError(f"Could not reach the Gmail API: {e}") from e
    raise GmailUnavailableError(f"Gmail API request failed after retries: {last_error}")


async def _get_service():
    creds = _load_credentials()
    if not creds:
        raise GmailAuthError(
            "No Gmail authorization found. Authenticate once by running: "
            "python test_gmail.py (or call /api/emails/re-auth)."
        )
    try:
        await ensure_refreshed(creds)
    except GoogleAuthError as e:
        raise GmailAuthError(str(e)) from e
    return build_service("gmail", "v1", creds)


_thread_local = threading.local()


def _service_for_thread(creds):
    service = getattr(_thread_local, "service", None)
    if service is None:
        service = build_service("gmail", "v1", creds)
        _thread_local.service = service
    return service


def _header(headers, name):
    for h in headers or []:
        if h["name"].lower() == name.lower():
            return h["value"]
    return None


def _split_sender(value):
    if not value:
        return None, None
    if "<" in value and ">" in value:
        name = value.split("<")[0].strip().strip('"') or None
        addr = value.split("<")[1].split(">")[0].strip()
        return addr, name
    return value.strip(), None


def _get_body_text(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return _html_to_text(_decode(payload["body"]["data"]))
    for part in payload.get("parts", []):
        text = _get_body_text(part)
        if text:
            return text
    return None


def _get_body_html(payload):
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []):
        html = _get_body_html(part)
        if html:
            return html
    return None


def _decode(data):
    if not data:
        return None
    try:
        raw = base64.urlsafe_b64decode(data.encode("ascii"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


_BLOCK_TAGS = {
    "p", "div", "br", "tr", "td", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "section", "article", "header", "footer", "blockquote", "pre", "hr",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self._space_pending = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self._space_pending = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self._space_pending = True

    def handle_data(self, data):
        if self.skip:
            return
        if self.parts and not self._space_pending and not self.parts[-1].endswith("\n"):
            self.parts.append(" ")
        self._space_pending = False
        self.parts.append(data.strip())


def _html_to_text(raw: str) -> str:
    if not raw:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw)
    text = " ".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return html_lib.unescape(text).strip()


def _parse_message(full: dict) -> dict:
    payload = full.get("payload", {})
    headers = payload.get("headers", [])
    sender_addr, sender_name = _split_sender(_header(headers, "From"))
    received_raw = _header(headers, "Date")
    try:
        received_at = parsedate_to_datetime(received_raw)
        if received_at.tzinfo is not None:
            received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        received_at = None
    return {
        "message_id": full.get("id"),
        "thread_id": full.get("threadId"),
        "from_email": sender_addr,
        "from_name": sender_name,
        "to_email": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "body_text": _get_body_text(payload),
        "body_html": _get_body_html(payload),
        "received_at": received_at,
        "is_read": "UNREAD" not in full.get("labelIds", []),
    }


async def fetch_recent_emails(max_results: int = 50) -> list[dict]:
    service = await _get_service()
    results = await _call_with_retry(
        lambda: service.users().messages().list(
            userId="me", maxResults=max_results
        )
    )
    message_ids = [m["id"] for m in results.get("messages", [])]

    creds = _load_credentials()
    fetched = []
    for mid in message_ids:
        await ensure_refreshed(creds)
        full = await _call_with_retry(
            lambda: _service_for_thread(creds).users().messages().get(
                userId="me", id=mid, format="full"
            )
        )
        fetched.append(_parse_message(full))
    return fetched


async def sync_emails(max_results: int = 50, db: Session = None) -> list[int]:
    fetched = await fetch_recent_emails(max_results)
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    added_ids: list[int] = []
    try:
        existing_ids = {e.message_id for e in db.query(Email.message_id).all()}
        for data in fetched:
            if not data["message_id"] or data["message_id"] in existing_ids:
                continue
            data["retention_until"] = data.get("retention_until") or (
                datetime.datetime.utcnow() + datetime.timedelta(days=DEFAULT_EMAIL_RETENTION_DAYS)
            )
            data["sensitivity"] = data.get("sensitivity") or DEFAULT_EMAIL_SENSITIVITY
            email = Email(**data)
            db.add(email)
            db.flush()
            added_ids.append(email.id)
        db.commit()
    finally:
        if owns_db:
            db.close()
    return added_ids


_profile_email: Optional[str] = None
_profile_email_ts = 0.0
_PROFILE_CACHE_TTL = 3600
_PROFILE_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "account_email.json")


def _load_profile_cache():
    global _profile_email, _profile_email_ts
    try:
        if os.path.exists(_PROFILE_CACHE_FILE):
            with open(_PROFILE_CACHE_FILE) as f:
                data = json.load(f)
            _profile_email = data.get("email")
            _profile_email_ts = data.get("ts", 0.0)
    except Exception:
        pass


def _save_profile_cache(email: str):
    try:
        os.makedirs(os.path.dirname(_PROFILE_CACHE_FILE), exist_ok=True)
        with open(_PROFILE_CACHE_FILE, "w") as f:
            json.dump({"email": email, "ts": time.time()}, f)
    except Exception:
        pass


async def get_account_email() -> Optional[str]:
    global _profile_email, _profile_email_ts
    if not _profile_email:
        _load_profile_cache()
    if _profile_email and time.time() - _profile_email_ts < _PROFILE_CACHE_TTL:
        return _profile_email
    try:
        service = await _get_service()
        prof = await _call_with_retry(lambda: service.users().getProfile(userId="me"))
        _profile_email = prof.get("emailAddress")
        _profile_email_ts = time.time()
        if _profile_email:
            _save_profile_cache(_profile_email)
    except Exception:
        pass
    return _profile_email


async def auth_status() -> dict:
    creds = _load_credentials()
    if not creds:
        return {"connected": False, "reason": "not_authorized"}
    if creds.valid and not creds.expired:
        return {"connected": True, "reason": "ok"}
    if creds.refresh_token:
        try:
            await ensure_refreshed(creds)
            return {"connected": True, "reason": "refreshed"}
        except GoogleAuthError:
            return {"connected": False, "reason": "expired"}
    return {"connected": False, "reason": "invalid"}


async def health_check() -> dict:
    from app.services.google_auth import probe_credentials

    state = await probe_credentials()
    state["email"] = await get_account_email()
    return state


async def reauthorize() -> dict:
    try:
        await asyncio.to_thread(_google_reauthorize)
    except GoogleAuthError as e:
        raise GmailAuthError(str(e)) from e
    return {"connected": True}
