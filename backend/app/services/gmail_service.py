import asyncio
import base64
import datetime
import html as html_lib
import http.client
import json
import os
import random
import re
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Optional

import httplib2
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.models.email import Email
from app.core.database import SessionLocal
from app.core.config import settings
from app.services.google_auth import (
    GoogleAuthError,
    build_service,
    credentials_for_user,
    ensure_refreshed_for_user,
    integration_row,
)
from app.services.auth_state import AuthStateStore
from app.services.ownership import owner_user_id

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
BATCH_SIZE = 50


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
                    "Re-connect Google from the Settings page.",
                )
                raise GmailAuthError("Gmail authorization is invalid or expired")
            raise GmailUnavailableError(f"Gmail API error (HTTP {status})") from e
        except RefreshError as e:
            # The stored access token looked usable (no expiry) but the refresh
            # token is dead (e.g. revoked / "invalid_grant"). google-auth raises
            # this from inside ``request.execute()``; surface it as a graceful
            # auth error instead of an unhandled 500.
            AuthStateStore.mark_dead(
                reason="expired",
                last_error="Google authorization has expired and can't be refreshed. "
                "Re-connect Google from the Settings page.",
            )
            raise GmailAuthError(
                "Google authorization has expired and can't be refreshed. "
                "Re-connect Google from the Settings page."
            ) from e
        except (ConnectionError, TimeoutError, OSError, http.client.HTTPException, httplib2.HttpLib2Error, AttributeError) as e:
            if attempt < max_retries:
                delay = 2**attempt + random.uniform(0, 1)
                last_error = e
                await asyncio.sleep(min(delay, 30))
                continue
            raise GmailUnavailableError(f"Could not reach the Gmail API: {e}") from e
    raise GmailUnavailableError(f"Gmail API request failed after retries: {last_error}")


async def _get_user_service(db, user_id=None):
    user_id = user_id or owner_user_id(db)
    creds = await asyncio.to_thread(credentials_for_user, db, user_id)
    if not creds:
        raise GmailAuthError(
            f"No Google authorization found for account #{user_id}. "
            "Connect Google from the Settings page."
        )
    try:
        await ensure_refreshed_for_user(db, user_id, creds)
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
        "received_at": received_at,
        "is_read": "UNREAD" not in full.get("labelIds", []),
    }


async def fetch_recent_emails(max_results: int = 50, db=None, user_id=None, max_messages: int = None) -> tuple[list[dict], list[str]]:
    """
    Fetch recent emails with pagination support.

    Returns:
        tuple: (fetched_messages, failed_message_ids)
        - fetched_messages: list of successfully parsed email dicts
        - failed_message_ids: list of message IDs that failed to fetch/parse
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        user_id = user_id or owner_user_id(db)
        service = await _get_user_service(db, user_id)
        creds = await asyncio.to_thread(credentials_for_user, db, user_id)
        fetched = []
        failed_ids: list[str] = []
        page_token = None
        total_limit = max_messages if max_messages is not None else max_results
        # Ensure reasonable bounds
        if total_limit < 1:
            total_limit = max_results
        if total_limit > 1000:
            total_limit = 1000
        while len(fetched) + len(failed_ids) < total_limit:
            page_size = min(total_limit - len(fetched) - len(failed_ids), 100)
            results = await _call_with_retry(
                lambda pt=page_token, ps=page_size: service.users().messages().list(
                    userId="me",
                    maxResults=ps,
                    pageToken=pt,
                    includeSpamTrash=True,
                )
            )
            message_ids = [m["id"] for m in results.get("messages", [])]
            for mid in message_ids:
                try:
                    full = await _call_with_retry(
                        lambda m=mid: _service_for_thread(creds).users().messages().get(
                            userId="me", id=m, format="full"
                        )
                    )
                    fetched.append(_parse_message(full))
                except Exception as e:
                    # Log the failure but continue with other messages
                    import logging
                    logging.getLogger(__name__).warning(
                        "Failed to fetch/parse message %s for user %s: %s", mid, user_id, e
                    )
                    failed_ids.append(mid)
                if len(fetched) + len(failed_ids) >= total_limit:
                    break
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        return fetched, failed_ids
    finally:
        if owns_db:
            db.close()


async def sync_emails(max_results: int = 50, max_messages: int = None, db: Session = None, user_id: int = None) -> tuple[list[int], list[str]]:
    """
    Sync emails from Gmail to the database.

    Returns:
        tuple: (added_ids, failed_ids)
        - added_ids: list of database IDs for newly inserted emails
        - failed_ids: list of Gmail message IDs that failed to fetch/parse
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        user_id = user_id or owner_user_id(db)
        fetched, failed_ids = await fetch_recent_emails(max_results, db=db, user_id=user_id, max_messages=max_messages)
        added_ids: list[int] = []
        # Dedup is user-scoped: Gmail ids are unique per mailbox, so only the
        # given user's existing message ids are skipped. A fact of one user's
        # mailbox must never suppress an identical id belonging to another.
        existing_ids = {
            e.message_id
            for e in db.query(Email.message_id).filter(Email.user_id == user_id).all()
        }
        for data in fetched:
            if not data["message_id"] or data["message_id"] in existing_ids:
                continue
            data["retention_until"] = data.get("retention_until") or (
                datetime.datetime.utcnow() + datetime.timedelta(days=settings.EMAIL_RETENTION_DAYS)
            )
            data["sensitivity"] = data.get("sensitivity") or settings.EMAIL_SENSITIVITY
            data["user_id"] = user_id
            email = Email(**data)
            db.add(email)
            db.flush()
            added_ids.append(email.id)
        db.commit()
        return added_ids, failed_ids
    finally:
        if owns_db:
            db.close()


# Per-user in-memory cache: user_id -> {"email": str, "ts": float}.
# A global cache would let one user's resolved address leak into another
# user's dashboard, so both the memory and disk caches are keyed by user_id.
_profile_cache: dict = {}
_PROFILE_CACHE_TTL = 3600
_PROFILE_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "cache")


def _profile_cache_path(user_id: int) -> str:
    return os.path.join(_PROFILE_CACHE_DIR, f"account_email_{user_id}.json")


def _load_profile_cache(user_id: int):
    try:
        path = _profile_cache_path(user_id)
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            return {"email": data.get("email"), "ts": data.get("ts", 0.0)}
    except Exception:
        pass
    return None


def _save_profile_cache(user_id: int, email: str) -> None:
    try:
        os.makedirs(_PROFILE_CACHE_DIR, exist_ok=True)
        with open(_profile_cache_path(user_id), "w") as f:
            json.dump({"email": email, "ts": time.time()}, f)
    except Exception:
        pass


def _cached_account_email(user_id: int) -> Optional[str]:
    """Return a fresh-enough cached address for this user (memory then disk)."""
    cached = _profile_cache.get(user_id)
    if cached and time.time() - cached["ts"] < _PROFILE_CACHE_TTL:
        return cached["email"]
    disk = _load_profile_cache(user_id)
    if disk and disk["email"] and time.time() - disk["ts"] < _PROFILE_CACHE_TTL:
        _profile_cache[user_id] = disk
        return disk["email"]
    return None


async def get_account_email(db=None, user_id=None) -> Optional[str]:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        user_id = user_id or owner_user_id(db)

        # Preferred, user-scoped source: the requesting user's own integration.
        # A user can never resolve another user's email from here.
        row = integration_row(db, provider="google", user_id=user_id)
        if row is not None and row.account_id:
            return row.account_id

        cached = _cached_account_email(user_id)
        if cached:
            return cached

        email = None
        try:
            service = await _get_user_service(db, user_id)
            prof = await _call_with_retry(
                lambda: service.users().getProfile(userId="me")
            )
            email = prof.get("emailAddress")
            if email:
                _profile_cache[user_id] = {"email": email, "ts": time.time()}
                _save_profile_cache(user_id, email)
        except Exception:
            pass
        return email
    finally:
        if owns_db:
            db.close()


async def auth_status(db=None, user_id=None) -> dict:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        user_id = user_id or owner_user_id(db)
        creds = await asyncio.to_thread(credentials_for_user, db, user_id)
        if not creds:
            return {"connected": False, "reason": "not_authorized"}
        if creds.valid and not creds.expired:
            return {"connected": True, "reason": "ok"}
        if creds.refresh_token:
            try:
                await ensure_refreshed_for_user(db, user_id, creds)
                return {"connected": True, "reason": "refreshed"}
            except GoogleAuthError:
                return {"connected": False, "reason": "expired"}
        return {"connected": False, "reason": "invalid"}
    finally:
        if owns_db:
            db.close()


async def health_check(db=None, user_id=None) -> dict:
    from app.services.google_auth import probe_credentials_for_user

    state = await probe_credentials_for_user(db, user_id)
    state["email"] = await get_account_email(db=db, user_id=user_id)
    return state
