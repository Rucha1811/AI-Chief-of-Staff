"""CHARUSAT e-Governance attendance connector (read-only).

Why this connector never logs in
--------------------------------
The university portal (``https://support.charusat.edu.in/Egov/home.aspx``) is
an ASP.NET WebForms application whose login requires a username, a password AND
a CAPTCHA (``Captcha.ashx``). Automating that login would mean defeating a
security control the university put there deliberately, so it is not done here.
No officially supported API or integration endpoint was found either.

The two safe, user-initiated, read-only flows that replace it
------------------------------------------------------------
1. SNAPSHOT IMPORT - no credentials at all
   The student signs in themselves (solving the CAPTCHA), opens their
   attendance page and saves / copies the page HTML. The backend parses that
   HTML offline. Nothing secret is ever stored.

2. SESSION SYNC - user-provided session cookie
   The student signs in themselves and copies the session cookie their own
   browser is already using, plus the exact attendance URL they are viewing.
   The backend performs a read-only GET of that URL with that cookie. The
   cookie is stored encrypted (Fernet, the same ``SECRET_KEY`` used for Google
   credentials) and is never logged, never returned to the client and never
   shared between users.

Neither flow submits anything to the portal, edits attendance, or touches
faculty workflows. There is no login automation and no background polling:
refreshes happen only when the student asks for one.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import settings
from app.core.security import decrypt_bytes, encrypt_bytes
from app.core.timeutil import utcnow
from app.models.egov_attendance import EGovAttendance
from app.models.timetable import TimetableEntry
from app.models.user_integration import UserIntegration
from app.services import attendance_intelligence

logger = logging.getLogger("ai-chief.egov")

# The ``provider`` value that identifies the e-Governance connection on the
# shared ``user_integrations`` table. Distinct from "google", so disconnecting
# either one can never touch the other.
EGOV_PROVIDER = "charusat_egov"

_MAX_ERROR_CHARS = 500

# Markers that identify the portal's login page. Seeing any of them in a fetched
# page means the session is not (or is no longer) authenticated. It is never an
# attendance page, so we fail into a "reauthenticate" state instead of parsing
# nonsense into fake attendance records.
_LOGIN_MARKERS = (
    "txtPassword",
    "Captcha.ashx",
    "Gateway to e-Governance",
    "Enter Captcha Code Above",
    "/home.aspx",
)

_SESSION_EXPIRED_MSG = (
    "The e-Governance session is missing, expired or was rejected. Sign in to "
    "the portal again and provide a fresh session cookie (or import the saved "
    "attendance page)."
)


class EGovError(Exception):
    """Base class for connector failures (never carries the session cookie)."""


class EGovSessionExpired(EGovError):
    """The portal session is missing, invalid or expired."""


class EGovUnavailable(EGovError):
    """The portal is unreachable or returned a server error."""


class EGovParseError(EGovError):
    """No attendance-shaped table was found in the supplied page."""


# ---------------------------------------------------------------------------
# Normalised rows
# ---------------------------------------------------------------------------


@dataclass
class ParsedRow:
    term: str = ""
    subject_code: str = ""
    subject_name: str = ""
    classes_held: Optional[int] = None
    classes_attended: Optional[int] = None
    percentage: Optional[float] = None
    source_updated_at: Optional[datetime] = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            self.term.strip().lower(),
            self.subject_code.strip().lower(),
            self.subject_name.strip().lower(),
        )


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    skipped: int = 0


# ---------------------------------------------------------------------------
# HTML table extraction (standard library only - no new dependency)
# ---------------------------------------------------------------------------


class _TableExtractor(HTMLParser):
    """Collect top-level HTML tables as rows of ``(cell_text, is_header)``.

    Nested tables (ASP.NET controls) are passed through: their text is folded
    into the enclosing cell rather than being mistaken for new rows, because
    only a depth of one starts rows/cells.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, bool]]]] = []
        self._depth = 0
        self._rows: Optional[list[list[tuple[str, bool]]]] = None
        self._row: Optional[list[tuple[str, bool]]] = None
        self._cell: Optional[list[str]] = None
        self._cell_header = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
        elif self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = []
                self._cell_header = tag == "th"
            elif tag == "br" and self._cell is not None:
                self._cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            if self._depth == 1 and self._rows is not None:
                self.tables.append(self._rows)
                self._rows = None
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                text = " ".join("".join(self._cell).split())
                self._row.append((text, self._cell_header))
                self._cell = None
            elif tag == "tr" and self._row is not None and self._rows is not None:
                self._rows.append(self._row)
                self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


# ---------------------------------------------------------------------------
# Value parsers
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d %B %Y",
)


def parse_int(text: Optional[str]) -> Optional[int]:
    """First integer in a cell, or ``None``. Text with no digit is not a 0."""
    if text is None:
        return None
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    match = re.search(r"-?\d+", cleaned)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:  # pragma: no cover - the regex guarantees digits
        return None


def parse_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:  # pragma: no cover
        return None


def parse_date(text: Optional[str]) -> Optional[datetime]:
    """Parse a portal date, preferring unambiguous ISO then day-first formats.

    Returns ``None`` for absent or unrecognised values - the connector never
    guesses a date that the source did not publish.
    """
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _split_pair(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Interpret a cell as either a single number or a ``numerator/denominator``.

    ``"24/30"`` -> ``(24, 30)``; ``"30"`` -> ``(30, None)``; garbage -> both
    ``None``. Used so a combined "Attended/Total" cell still yields both values
    while a plain number keeps its own column's meaning.
    """
    if text is None:
        return None, None
    cleaned = text.replace(",", "").strip()
    if not cleaned:
        return None, None
    match = re.search(r"(-?\d+)\s*/\s*(-?\d+)", cleaned)
    if match:
        return int(match.group(1)), int(match.group(2))
    return parse_int(cleaned), None


def compute_percentage(
    attended: Optional[int], held: Optional[int]
) -> Optional[float]:
    """Attendance percentage from verified numerator and denominator only.

    ``None`` when either number is missing, the denominator is not positive, or
    ``attended > held`` (internally inconsistent source data). A value is never
    fabricated from missing data and a zero denominator is never divided.
    """
    if attended is None or held is None:
        return None
    if held <= 0 or attended < 0 or attended > held:
        return None
    return round(attended / held * 100.0, 1)


# ---------------------------------------------------------------------------
# Header classification
# ---------------------------------------------------------------------------

_PERCENT_EXACT = {"attendance", "att", "attendance pct", "attendance percentage"}


def classify_header(text: Optional[str]) -> Optional[str]:
    """Map a table header cell to a canonical attendance field, or ``None``.

    Rules are ordered so qualified headers win: "Lectures Attended" is
    ``classes_attended`` while "Total Lectures" / "Lectures Held" are
    ``classes_held``, and "Attendance %" is the percentage column.
    """
    if text is None:
        return None
    raw = text.strip()
    if not raw:
        return None
    low = raw.lower()
    if "%" in raw or "percent" in low or low in _PERCENT_EXACT:
        return "percentage"

    norm = re.sub(r"[^a-z0-9]+", " ", low).strip()
    words = set(norm.split())
    joined = norm.replace(" ", "")

    if not words:
        return None
    if "code" in words or "code" in joined:
        return "subject_code"
    if words & {"semester", "sem", "term"}:
        return "term"
    if words & {"held", "conducted", "delivered"}:
        return "classes_held"
    if "total" in words and "attend" not in norm and "present" not in norm:
        return "classes_held"
    if any(w in norm for w in ("lecture", "class", "period")):
        if "attend" in norm or "present" in norm:
            return "classes_attended"
        return "classes_held"
    if "attend" in norm or "present" in norm:
        return "classes_attended"
    if words & {"updated", "update", "revised"}:
        return "source_updated_at"
    if words & {"subject", "course", "paper", "name", "title", "particulars"}:
        return "subject_name"
    # Serial-number / index columns are deliberately ignored.
    if words <= {"sr", "s", "no", "sno", "sl", "index", "srno", "sno"}:
        return None
    return None


def _mapping_for_row(
    row: list[tuple[str, bool]],
) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for idx, (text, _is_header) in enumerate(row):
        field_name = classify_header(text)
        if field_name and field_name not in mapping.values():
            mapping[idx] = field_name
    return mapping


def _score_mapping(mapping: dict[int, str]) -> int:
    fields = set(mapping.values())
    if not ({"subject_name", "subject_code"} & fields):
        return 0
    score = len(fields)
    if "classes_held" in fields:
        score += 1
    if "classes_attended" in fields:
        score += 1
    if "percentage" in fields:
        score += 1
    return score


def _select_table(
    tables: list[list[list[tuple[str, bool]]]],
) -> tuple[Optional[list[list[tuple[str, bool]]]], int, dict[int, str]]:
    """Return the best attendance table, its header row index and column map."""
    best_score = 0
    best_table: Optional[list[list[tuple[str, bool]]]] = None
    best_row = -1
    best_mapping: dict[int, str] = {}
    for table in tables:
        for idx, row in enumerate(table):
            mapping = _mapping_for_row(row)
            score = _score_mapping(mapping)
            if score > best_score:
                best_score = score
                best_table = table
                best_row = idx
                best_mapping = mapping
    return best_table, best_row, best_mapping


def _build_row(
    cells: list[tuple[str, bool]], mapping: dict[int, str]
) -> Optional[ParsedRow]:
    values: dict[str, str] = {}
    for idx, field_name in mapping.items():
        if idx < len(cells):
            values[field_name] = cells[idx][0]
    if not values:
        return None

    name = (values.get("subject_name") or "").strip()
    code = (values.get("subject_code") or "").strip()
    if not name and not code:
        return None
    label = (name or code).strip().lower()
    if label in {"total", "grand total", "overall", "total attendance"}:
        return None

    held: Optional[int] = None
    attended: Optional[int] = None
    attended_hint: Optional[int] = None

    if "classes_held" in values:
        first, second = _split_pair(values["classes_held"])
        if second is not None:
            # A combined "a/b" cell in a lectures column is conventionally
            # "attended / total"; take the denominator as held and remember the
            # numerator in case no dedicated attended column exists.
            held = second
            attended_hint = first
        else:
            held = first
    if "classes_attended" in values:
        first, second = _split_pair(values["classes_attended"])
        attended = first
        if held is None and second is not None:
            held = second
    if attended is None and attended_hint is not None:
        attended = attended_hint

    percentage: Optional[float] = None
    if "percentage" in values:
        printed = parse_float(values["percentage"])
        if printed is not None and 0.0 <= printed <= 100.0:
            percentage = printed

    computed = compute_percentage(attended, held)
    if computed is not None:
        # A percentage derived from verified counts always wins over a printed
        # one, so the two can never disagree in the UI.
        percentage = computed

    return ParsedRow(
        term=(values.get("term") or "").strip(),
        subject_code=code,
        subject_name=name or code,
        classes_held=held,
        classes_attended=attended,
        percentage=percentage,
        source_updated_at=parse_date(values.get("source_updated_at")),
    )


def looks_like_login(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _LOGIN_MARKERS)


def parse_attendance_html(html: str) -> ParseResult:
    """Parse portal HTML into normalised, de-duplicated subject-level rows.

    Raises ``EGovSessionExpired`` when the page is the portal login page and
    ``EGovParseError`` when no attendance table exists. A recognised table with
    zero data rows is not an error: it means the source genuinely had no
    records, and is reported as an empty result.
    """
    if not html or not html.strip():
        raise EGovParseError("The supplied page was empty.")
    if looks_like_login(html):
        raise EGovSessionExpired(_SESSION_EXPIRED_MSG)

    parser = _TableExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # pragma: no cover - defensive
        raise EGovParseError("The supplied page could not be read as HTML.") from exc

    table, header_index, mapping = _select_table(parser.tables)
    if table is None or not mapping:
        raise EGovParseError(
            "No attendance table was recognised in the supplied page. Open "
            "your attendance page in e-Governance and import that page."
        )

    rows_by_key: dict[tuple[str, str, str], ParsedRow] = {}
    skipped = 0
    for cells in table[header_index + 1 :]:
        row = _build_row(cells, mapping)
        if row is None:
            skipped += 1
            continue
        # Duplicate natural keys keep the LAST occurrence (a later rendering
        # supersedes an earlier one); they are never summed, which could
        # double-count.
        rows_by_key[row.key] = row
    return ParseResult(rows=list(rows_by_key.values()), skipped=skipped)


# ---------------------------------------------------------------------------
# Read-only fetching (bounded, host-pinned)
# ---------------------------------------------------------------------------


def _host_allowed(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and host in settings.egov_allowed_hosts


def validate_attendance_url(url: str) -> str:
    """Normalise an attendance URL and pin it to the university host.

    Prevents the endpoint from being turned into an SSRF probe: only http(s)
    URLs on a configured e-Governance host are accepted, and an empty allowlist
    fails closed.
    """
    if not url or not url.strip():
        raise EGovError("An attendance page URL is required.")
    candidate = url.strip()
    if not candidate.lower().startswith(("http://", "https://")):
        raise EGovError("The attendance URL must start with http:// or https://")
    if not _host_allowed(candidate):
        raise EGovError(
            "The attendance URL must be on the university e-Governance host "
            f"({settings.EGOV_BASE_URL})."
        )
    return candidate


def _get_following_same_host(client: httpx.Client, url: str) -> httpx.Response:
    """Follow a few redirects, refusing to leave the university host."""
    current = url
    for _ in range(4):
        response = client.get(current)
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        target = urljoin(current, location)
        if looks_like_login(target):
            raise EGovSessionExpired(_SESSION_EXPIRED_MSG)
        if not _host_allowed(target):
            raise EGovError("The portal redirected off the university host.")
        current = target
    raise EGovError("The e-Governance portal returned too many redirects.")


def fetch_attendance_html(
    url: str,
    session_cookie: str,
    *,
    client: Optional[httpx.Client] = None,
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
) -> str:
    """Read-only GET of the attendance page with bounded retries.

    Only transient failures (timeout, transport error, 5xx) are retried, with
    exponential backoff. A rejected session (401/403), a redirect to the login
    page, or a 404 fails immediately: retrying would only hammer the university
    server for a request that cannot succeed.
    """
    url = validate_attendance_url(url)
    retries = settings.EGOV_MAX_RETRIES if max_retries is None else max_retries
    attempts = max(1, retries + 1)
    delay = settings.EGOV_RETRY_BASE_DELAY if base_delay is None else base_delay

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=httpx.Timeout(settings.EGOV_REQUEST_TIMEOUT),
            follow_redirects=False,
            headers={
                "User-Agent": "AIChiefOfStaff/1.0 (personal attendance reader)",
                "Accept": "text/html,application/xhtml+xml",
                "Cookie": session_cookie or "",
            },
        )

    last_error: Optional[EGovError] = None
    try:
        for attempt in range(attempts):
            try:
                response = _get_following_same_host(client, url)
            except httpx.TimeoutException:
                last_error = EGovUnavailable("The e-Governance portal timed out.")
            except httpx.TransportError:
                last_error = EGovUnavailable("The e-Governance portal is unreachable.")
            else:
                status = response.status_code
                if status == 200:
                    if looks_like_login(response.text):
                        raise EGovSessionExpired(_SESSION_EXPIRED_MSG)
                    return response.text
                if status in (401, 403):
                    raise EGovSessionExpired(_SESSION_EXPIRED_MSG)
                if status == 404:
                    raise EGovError(
                        "The attendance URL returned 404. Check the address you "
                        "copied from your browser while signed in."
                    )
                if 500 <= status < 600:
                    last_error = EGovUnavailable(
                        f"The e-Governance portal returned {status}."
                    )
                else:
                    raise EGovError(
                        f"The attendance page returned an unexpected {status} response."
                    )
            if attempt < attempts - 1:
                time.sleep(delay * (2**attempt))
    finally:
        if owns_client:
            client.close()

    raise last_error or EGovUnavailable("The e-Governance portal is unavailable.")


# ---------------------------------------------------------------------------
# Persistence / orchestration (all queries are scoped to one user)
# ---------------------------------------------------------------------------


def get_connection(db, user_id: int) -> Optional[UserIntegration]:
    return (
        db.query(UserIntegration)
        .filter(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == EGOV_PROVIDER,
        )
        .first()
    )


def _connection_meta(row: Optional[UserIntegration]) -> dict:
    if row is None or not isinstance(row.meta, dict):
        return {}
    return dict(row.meta)


def _decrypt_cookie(row: UserIntegration) -> str:
    if not row.refresh_token_encrypted:
        raise EGovError("No stored e-Governance session; connect first.")
    try:
        return decrypt_bytes(
            row.refresh_token_encrypted.encode(), settings.SECRET_KEY
        ).decode()
    except Exception as exc:  # wrong key or corrupt record
        raise EGovError(
            "The stored e-Governance session could not be read. Please reconnect."
        ) from exc


def _replace_snapshot(
    db,
    user_id: int,
    rows: list[ParsedRow],
    *,
    term: Optional[str],
    source_reference: Optional[str],
) -> None:
    """Replace the user's snapshot atomically with a freshly parsed one."""
    db.query(EGovAttendance).filter(EGovAttendance.user_id == user_id).delete(
        synchronize_session=False
    )
    now = utcnow()
    for row in rows:
        db.add(
            EGovAttendance(
                user_id=user_id,
                term=((term if term is not None else row.term) or "")[:64],
                subject_code=(row.subject_code or "")[:64],
                subject_name=(row.subject_name or "")[:256],
                classes_held=row.classes_held,
                classes_attended=row.classes_attended,
                percentage=row.percentage,
                source_updated_at=row.source_updated_at,
                synced_at=now,
                source_reference=(source_reference or "")[:512] or None,
            )
        )
    db.commit()


def _mark_failure(db, user_id: int, status: str, message: str) -> None:
    """Record a failure without touching the last known-good snapshot."""
    row = get_connection(db, user_id)
    if row is None:
        return
    row.status = status
    row.last_error = (message or "")[:_MAX_ERROR_CHARS]
    db.commit()


def _mark_success(
    db,
    user_id: int,
    *,
    method: str,
    attendance_url: Optional[str],
    subject_count: int,
) -> None:
    row = get_connection(db, user_id)
    if row is None:
        return
    row.status = "active"
    row.last_error = None
    row.last_synced_at = utcnow()
    meta = _connection_meta(row)
    if attendance_url:
        meta["attendance_url"] = attendance_url
    if method == "session":
        meta["method"] = "session"
    elif meta.get("method") != "session":
        meta["method"] = "import"
    meta["last_sync_method"] = method
    meta["subject_count"] = subject_count
    row.meta = meta
    db.commit()


def save_session(
    db,
    user_id: int,
    *,
    session_cookie: str,
    attendance_url: str,
    account_id: Optional[str] = None,
) -> None:
    """Store an encrypted session cookie and the attendance URL for a user."""
    url = validate_attendance_url(attendance_url)
    cookie = (session_cookie or "").strip()
    if not cookie:
        raise EGovError("A session cookie is required.")

    row = get_connection(db, user_id)
    if row is None:
        row = UserIntegration(user_id=user_id, provider=EGOV_PROVIDER)
        db.add(row)
    row.account_id = (account_id or "").strip() or row.account_id
    row.status = "active"
    row.refresh_token_encrypted = encrypt_bytes(
        cookie.encode(), settings.SECRET_KEY
    ).decode()
    row.last_error = None
    meta = _connection_meta(row)
    meta["method"] = "session"
    meta["attendance_url"] = url
    row.meta = meta
    db.commit()


def import_html(
    db,
    user_id: int,
    *,
    html: str,
    term: Optional[str] = None,
    source_reference: str = "import",
) -> ParseResult:
    """Parse and store a saved attendance page (no credentials involved)."""
    result = parse_attendance_html(html)

    row = get_connection(db, user_id)
    if row is None:
        row = UserIntegration(user_id=user_id, provider=EGOV_PROVIDER, status="active")
        db.add(row)
        db.commit()
        row = get_connection(db, user_id)

    _replace_snapshot(
        db, user_id, result.rows, term=term, source_reference=source_reference
    )
    _mark_success(
        db,
        user_id,
        method="import",
        attendance_url=None,
        subject_count=len(result.rows),
    )
    return result


def sync_for_user(db, user_id: int) -> ParseResult:
    """Refresh the snapshot using the stored session cookie.

    On any failure the existing snapshot is preserved and the failure is
    recorded on the connection, so the UI can show the last known-good data
    with a stale/unavailable marker instead of an empty screen.
    """
    row = get_connection(db, user_id)
    if row is None:
        raise EGovError("e-Governance attendance is not connected.")

    meta = _connection_meta(row)
    url = meta.get("attendance_url")
    if not url:
        message = (
            "No attendance URL is stored for this connection. Reconnect with "
            "the exact address you see while signed in."
        )
        _mark_failure(db, user_id, "error", message)
        raise EGovError(message)

    try:
        cookie = _decrypt_cookie(row)
    except EGovError as exc:
        _mark_failure(db, user_id, "error", str(exc))
        raise

    try:
        html = fetch_attendance_html(url, cookie)
    except EGovSessionExpired as exc:
        _mark_failure(db, user_id, "expired", str(exc))
        raise
    except EGovError as exc:
        _mark_failure(db, user_id, "error", str(exc))
        raise

    try:
        result = parse_attendance_html(html)
    except EGovSessionExpired as exc:
        _mark_failure(db, user_id, "expired", str(exc))
        raise
    except EGovParseError as exc:
        _mark_failure(db, user_id, "error", str(exc))
        raise

    _replace_snapshot(db, user_id, result.rows, term=None, source_reference=url)
    _mark_success(db, user_id, method="session", attendance_url=url, subject_count=len(result.rows))
    return result


def disconnect(db, user_id: int) -> bool:
    """Remove the connection and its snapshot. Never touches other providers."""
    row = get_connection(db, user_id)
    db.query(EGovAttendance).filter(EGovAttendance.user_id == user_id).delete(
        synchronize_session=False
    )
    if row is not None:
        db.delete(row)
    db.commit()
    return row is not None


def _serialize_record(record: EGovAttendance) -> dict:
    return {
        "id": record.id,
        "term": record.term or "",
        "subject_code": record.subject_code or "",
        "subject_name": record.subject_name or "",
        "classes_held": record.classes_held,
        "classes_attended": record.classes_attended,
        "percentage": record.percentage,
        "source_updated_at": (
            record.source_updated_at.isoformat() if record.source_updated_at else None
        ),
        "synced_at": record.synced_at.isoformat() if record.synced_at else None,
    }


def status_for_user(db, user_id: int) -> dict:
    """Return connection state + snapshot rows for one user (never other users')."""
    row = get_connection(db, user_id)
    records = (
        db.query(EGovAttendance)
        .filter(EGovAttendance.user_id == user_id)
        .order_by(EGovAttendance.subject_name.asc(), EGovAttendance.subject_code.asc())
        .all()
    )

    last_synced_at = row.last_synced_at if row is not None else None
    stale = True
    if last_synced_at is not None:
        stale = (utcnow() - last_synced_at) > timedelta(
            hours=settings.EGOV_STALE_AFTER_HOURS
        )

    connected = row is not None and row.status not in ("revoked", "disconnected")
    meta = _connection_meta(row)

    if not connected:
        state = "not_connected"
    elif row.status == "expired":
        state = "reauth_required"
    elif row.status == "error":
        state = "unavailable"
    elif stale:
        state = "stale"
    else:
        state = "connected"

    held_total = 0
    attended_total = 0
    for record in records:
        if record.classes_held is not None and record.classes_attended is not None:
            held_total += record.classes_held
            attended_total += record.classes_attended
    overall = (
        round(attended_total / held_total * 100.0, 1) if held_total > 0 else None
    )

    serialized = [_serialize_record(record) for record in records]

    # Dashboard analysis (75% target, recovery maths, lecture priorities). It is
    # derived strictly from the rows above plus this user's own timetable - no
    # other user's data is read, and missing data is reported, not guessed.
    timetable = (
        db.query(TimetableEntry)
        .filter(TimetableEntry.user_id == user_id, TimetableEntry.active.is_(True))
        .order_by(TimetableEntry.day_of_week, TimetableEntry.start_time)
        .all()
    )
    analysis = attendance_intelligence.analyze(
        serialized,
        [
            {
                "title": entry.title,
                "location": entry.location,
                "day_of_week": entry.day_of_week,
                "start_time": entry.start_time,
                "end_time": entry.end_time,
            }
            for entry in timetable
        ],
        target=settings.ATTENDANCE_TARGET_PERCENTAGE,
        now=utcnow(),
    )

    return {
        "connected": connected,
        "state": state,
        "method": meta.get("method"),
        "account": row.account_id if row is not None else None,
        "status": row.status if row is not None else None,
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "stale": stale if connected else False,
        "last_error": row.last_error if row is not None else None,
        "record_count": len(records),
        "overall_percentage": overall,
        "holdings": {"held": held_total, "attended": attended_total},
        # "student_verified" means an authorized retrieval actually returned
        # attendance for this account - the address domain alone never claims it.
        "student_verified": len(records) > 0,
        "records": serialized,
        "analysis": analysis,
    }
