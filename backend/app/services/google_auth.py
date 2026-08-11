import os
import pickle
import threading
from typing import Optional

from google.auth.credentials import Credentials
from google.auth.exceptions import RefreshError
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.core.config import settings
from app.core.security import decrypt_bytes, encrypt_bytes
from app.services.auth_state import AuthStateStore

_HTTP_TIMEOUT = 15
_refresh_lock = threading.Lock()

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar"
SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"

# Superset → covered scopes. A token granted a superset scope satisfies
# requests for the narrower scopes (e.g. old "calendar" tokens keep working
# after we switched to requesting only "calendar.events").
_SCOPE_SUPERSETS = {
    SCOPE_CALENDAR: {SCOPE_CALENDAR_EVENTS, SCOPE_CALENDAR_READONLY},
}

# Minimal scopes for the enabled features (least privilege):
#  - Gmail: read only (summarize, detect deadlines, schedule)
#  - Classroom: read only
#  - Calendar: read + create events only (no settings/delete)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    SCOPE_CALENDAR_EVENTS,
]

CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "credentials")
CREDENTIALS_FILE = os.path.join(CREDENTIALS_DIR, "credentials.json")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "token.pickle")


class GoogleAuthError(RuntimeError):
    pass


def load_credentials() -> Optional[Credentials]:
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "rb") as f:
            raw = f.read()
        try:
            creds = pickle.loads(raw)
        except Exception:
            creds = pickle.loads(decrypt_bytes(raw, settings.SECRET_KEY))
        save_credentials(creds)
        return creds
    except Exception:
        return None


def save_credentials(creds: Credentials) -> None:
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    with open(TOKEN_FILE, "wb") as f:
        f.write(encrypt_bytes(pickle.dumps(creds), settings.SECRET_KEY))


def has_scope(creds: Credentials, scope: str) -> bool:
    scopes = set(getattr(creds, "scopes", None) or [])
    if scope in scopes:
        return True
    return any(scope in _SCOPE_SUPERSETS.get(s, ()) for s in scopes)


def build_service(service_name: str, version: str, creds: Credentials):
    import httplib2

    from google_auth_httplib2 import AuthorizedHttp

    http = AuthorizedHttp(
        credentials=creds,
        http=httplib2.Http(timeout=_HTTP_TIMEOUT),
    )
    return build(
        service_name,
        version,
        http=http,
        cache_discovery=False,
    )


def reauthorize() -> Credentials:
    if not os.path.exists(CREDENTIALS_FILE):
        raise GoogleAuthError(
            "Google credentials.json not found. Place your OAuth client "
            "credentials file at backend/credentials/credentials.json"
        )
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)

    import socket
    import threading
    import urllib.parse
    import webbrowser

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(300)
    port = srv.getsockname()[1]
    srv.listen(1)

    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    result = {}

    def _wait_for_code():
        try:
            conn, _ = srv.accept()
            data = conn.recv(4096).decode(errors="ignore")
            path = data.split(" ")[1] if " " in data else "/"
            body = b"<html><body><h2>Authorization complete.</h2><p>You can close this tab and return to AI Chief.</p></body></html>"
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            conn.close()
            result["query"] = urllib.parse.urlparse(path).query
        except Exception as e:
            result["error"] = str(e)
        finally:
            srv.close()

    webbrowser.open(auth_url)
    thread = threading.Thread(target=_wait_for_code, daemon=True)
    thread.start()
    thread.join()

    if "error" in result:
        raise GoogleAuthError(f"Could not complete the authorization flow: {result['error']}")

    params = urllib.parse.parse_qs(result["query"])
    code = params.get("code", [None])[0]
    if not code:
        raise GoogleAuthError(
            f"Authorization failed: {params.get('error', ['unknown error'])[0]}"
        )

    import requests
    from google.oauth2.credentials import Credentials

    resp = requests.post(
        flow.client_config["token_uri"],
        data={
            "client_id": flow.client_config["client_id"],
            "client_secret": flow.client_config["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": flow.redirect_uri,
        },
        timeout=30,
    )
    data = resp.json()
    if "access_token" not in data:
        raise GoogleAuthError(f"Token exchange failed: {data}")

    creds = Credentials(
        token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        token_uri=flow.client_config["token_uri"],
        client_id=flow.client_config["client_id"],
        client_secret=flow.client_config["client_secret"],
        scopes=(data.get("scope", "") or "").split(),
    )
    save_credentials(creds)
    AuthStateStore.renew_expiry(expires_at=creds.expiry)
    return creds


def _refresh_creds(creds: Credentials) -> None:
    from google.auth.transport.requests import Request

    with _refresh_lock:
        if not creds.valid:
            creds.refresh(Request())
            save_credentials(creds)


async def ensure_refreshed(creds: Credentials) -> None:
    import asyncio

    if creds.valid:
        return
    if not (creds.expired and creds.refresh_token):
        AuthStateStore.mark_dead(
            reason="invalid",
            last_error="Google authorization is invalid (missing refresh token).",
        )
        raise GoogleAuthError(
            "Google authorization is invalid. Please re-authenticate via /api/emails/re-auth."
        )
    try:
        await asyncio.wait_for(asyncio.to_thread(_refresh_creds, creds), timeout=30.0)
        AuthStateStore.renew_expiry(expires_at=creds.expiry)
    except (asyncio.TimeoutError, RefreshError) as e:
        AuthStateStore.mark_dead(reason="expired", last_error=str(e))
        raise GoogleAuthError(
            "Google authorization has expired and can't be refreshed. "
            "Re-authenticate via /api/emails/re-auth or run: python test_gmail.py"
        ) from e
    except Exception as e:
        raise GoogleAuthError(f"Could not refresh Google authorization: {e}") from e


async def probe_credentials() -> dict:
    """Check Google authorization health and persist the result.

    Never raises: returns a status dict the API/frontend can surface."""
    creds = load_credentials()
    if not creds:
        AuthStateStore.set_state("not_authorized", reason="not_authorized")
        return {
            "connected": False,
            "state": "not_authorized",
            "reason": "not_authorized",
            "last_error": (
                "No Google authorization found. Authenticate via /api/emails/re-auth "
                "or run: python test_gmail.py"
            ),
            "expires_at": None,
            "last_checked_at": None,
            "refresh_attempts": 0,
            "has_refresh_token": False,
            "scopes": [],
        }
    try:
        await ensure_refreshed(creds)
        AuthStateStore.renew_expiry(expires_at=creds.expiry)
    except GoogleAuthError:
        pass
    info = AuthStateStore.get_state()
    info["has_refresh_token"] = bool(creds.refresh_token)
    info["scopes"] = list(getattr(creds, "scopes", None) or [])
    return info
