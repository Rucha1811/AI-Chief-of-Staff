import asyncio
import logging

from sqlalchemy.orm import Session

from app.services.google_auth import (
    GoogleAuthError,
    build_service,
    ensure_refreshed,
    has_scope,
    load_credentials,
)
from app.services.auth_state import AuthStateStore

logger = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

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


async def _get_service():
    creds = load_credentials()
    if not creds:
        raise CalendarAuthError(
            "No Google authorization found. Authenticate via /api/emails/re-auth."
        )
    try:
        await ensure_refreshed(creds)
    except GoogleAuthError as e:
        raise CalendarAuthError(str(e)) from e
    if not has_scope(creds, CALENDAR_SCOPE):
        raise CalendarAuthError(
            "Calendar permission not granted. Re-authorize your Google account via "
            "/api/emails/re-auth so the Calendar scope is included, then try again."
        )
    return build_service("calendar", "v3", creds)


def _color_for_event(event) -> str:
    if getattr(event, "is_conflict", False):
        return CONFLICT_COLOR
    return EVENT_COLOR_BY_SOURCE.get(getattr(event, "source", ""), DEFAULT_COLOR)


def _to_rfc3339(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


async def push_events_to_calendar(db: Session, events) -> dict:
    service = await _get_service()

    created = 0
    skipped = 0
    failed = 0
    for event in events:
        if event.calendar_event_id:
            skipped += 1
            continue
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
