import asyncio
import logging

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.google_auth import (
    SCOPE_CALENDAR_EVENTS,
    GoogleAuthError,
    build_service,
    credentials_for_user,
    ensure_refreshed_for_user,
    has_scope,
)
from app.services.auth_state import AuthStateStore
from app.services.ownership import owner_user_id

logger = logging.getLogger(__name__)

# Minimal privilege: read + create events only. Old tokens granted the full
# "calendar" scope still satisfy this via has_scope's superset handling.
CALENDAR_SCOPE = SCOPE_CALENDAR_EVENTS

EVENT_COLOR_BY_SOURCE = {
    "classroom": "5",
    "teams": "9",
    "timetable": "7",
    "study": "6",
    "google": "4",
}
CONFLICT_COLOR = "11"
DEFAULT_COLOR = "1"


class CalendarAuthError(RuntimeError):
    pass


class CalendarPushError(RuntimeError):
    pass


async def _get_user_service(db: Session, user_id: int = None):
    user_id = user_id or owner_user_id(db)
    creds = await asyncio.to_thread(credentials_for_user, db, user_id)
    if not creds:
        raise CalendarAuthError(
            f"No Google authorization found for account #{user_id}. "
            "Connect Google from the Settings page."
        )
    try:
        await ensure_refreshed_for_user(db, user_id, creds)
    except GoogleAuthError as e:
        raise CalendarAuthError(str(e)) from e
    if not has_scope(creds, CALENDAR_SCOPE):
        raise CalendarAuthError(
            "Calendar permission not granted. Re-connect your Google account "
            "from the Settings page so the Calendar scope is included, then try again."
        )
    return build_service("calendar", "v3", creds)


def _color_for_event(event) -> str:
    if getattr(event, "is_conflict", False):
        return CONFLICT_COLOR
    return EVENT_COLOR_BY_SOURCE.get(getattr(event, "source", ""), DEFAULT_COLOR)


def _to_rfc3339(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


async def _list_existing_events(service, time_min: str, time_max: str, max_pages: int = 10) -> list[dict]:
    """Read existing events from the primary calendar for dedup/conflict checks.

    Returns a list of event dicts with at least ``id``, ``summary``, ``start``,
    ``end`` keys. Best-effort: returns [] on any failure so push can proceed.

    Paginates through all pages up to ``max_pages`` to handle calendars with
    many events in the time window.
    """
    try:
        events = []
        page_token = None
        pages_fetched = 0
        while pages_fetched < max_pages:
            result = await asyncio.to_thread(
                lambda pt=page_token: service.events().list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    maxResults=250,
                    pageToken=pt,
                ).execute()
            )
            events.extend(result.get("items", []) or [])
            page_token = result.get("nextPageToken")
            pages_fetched += 1
            if not page_token:
                break
        return events
    except Exception as e:
        logger.warning("Could not list existing calendar events: %s", e)
        return []


async def push_events_to_calendar(db: Session, events, user_id: int = None) -> dict:
    service = await _get_user_service(db, user_id)

    created = 0
    skipped = 0
    failed = 0
    for event in events:
        # Skip events already pushed to Google (source == google means pushed)
        if event.calendar_event_id and event.source == "google":
            skipped += 1
            continue

        # Read-before-write: check for duplicates in the same window
        try:
            time_min = event.start_time.isoformat()
            time_max = event.end_time.isoformat()
            existing = await _list_existing_events(service, time_min, time_max)
            duplicate = False
            for ex in existing:
                # Match by title AND start time (within 1 minute tolerance)
                # Also check if the existing event has a source_id that matches ours
                ex_start = ex.get("start", {}).get("dateTime") or ex.get("start", {}).get("date")
                if ex_start and ex.get("summary") == event.title:
                    try:
                        from datetime import datetime
                        ex_dt = datetime.fromisoformat(ex_start.replace("Z", "+00:00"))
                        # Compare within 1 minute tolerance
                        if abs((ex_dt - event.start_time).total_seconds()) < 60:
                            event.calendar_event_id = ex.get("id")
                            event.source = "google"
                            duplicate = True
                            break
                    except Exception:
                        # If parsing fails, fall back to title-only match
                        if ex.get("summary") == event.title:
                            event.calendar_event_id = ex.get("id")
                            event.source = "google"
                            duplicate = True
                            break
            if duplicate:
                skipped += 1
                continue
        except Exception as e:
            logger.warning("Read-before-write failed for %s: %s", event.title, e)

        body = {
            "summary": event.title,
            "start": {"dateTime": _to_rfc3339(event.start_time), "timeZone": "UTC"},
            "end": {"dateTime": _to_rfc3339(event.end_time), "timeZone": "UTC"},
            "colorId": _color_for_event(event),
        }
        if event.location:
            body["location"] = event.location
        if event.description:
            body["description"] = event.description
        if event.is_all_day:
            body["start"] = {"date": event.start_time.date().isoformat()}
            body["end"] = {"date": (event.end_time.date() + __import__("datetime").timedelta(days=1)).isoformat()}

        try:
            request = service.events().insert(calendarId="primary", body=body)
            result = await asyncio.to_thread(request.execute)
            event.calendar_event_id = result.get("id")
            event.source = "google"
            created += 1
        except Exception as e:
            logger.warning("Failed to push event %s to Google Calendar: %s", event.title, e)
            failed += 1

    db.commit()
    return {"created": created, "skipped": skipped, "failed": failed}
