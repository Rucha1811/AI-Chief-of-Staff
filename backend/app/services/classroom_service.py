import asyncio
import logging
from datetime import date, datetime, time

from google.auth.exceptions import RefreshError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.assignment import Assignment
from app.models.event import Event
from app.models.task import Task
from app.services.google_auth import (
    GoogleAuthError,
    build_service,
    credentials_for_user,
    ensure_refreshed_for_user,
    has_scope,
)
from app.services.auth_state import AuthStateStore
from app.services.ownership import owner_user_id

logger = logging.getLogger(__name__)

CLASSROOM_COURSES_SCOPE = "https://www.googleapis.com/auth/classroom.courses.readonly"
# ``courses.courseWork.list`` (used by list_assignments/list_submissions to read
# assignment titles and due dates) is authorized ONLY by a coursework scope.
# ``classroom.student-submissions.me.readonly`` does NOT cover it — verified
# against the Classroom v1 discovery document
# (https://classroom.googleapis.com/$discovery/rest?version=v1). Both scopes
# must be present before the coursework features can run.
CLASSROOM_COURSEWORK_SCOPE = "https://www.googleapis.com/auth/classroom.coursework.me.readonly"

# Tracked courses are org-scoped, deployment-specific data. They must be set
# via CLASSROOM_COURSE_IDS in .env; no course IDs belong in code. When empty,
# all courses from the connected Classroom account are tracked.
CURRENT_SEMESTER_COURSE_IDS = set(settings.classroom_course_ids)


class ClassroomAuthError(RuntimeError):
    pass


class ClassroomUnavailableError(RuntimeError):
    pass


class ClassroomScopeError(RuntimeError):
    """A feature needs a scope the granted token does not carry.

    Distinct from ClassroomAuthError (no/invalid token) so the sync layer can
    skip the feature WITHOUT marking the whole integration dead over the wire.
    """


async def _get_user_service(db: Session, user_id: int = None):
    user_id = user_id or owner_user_id(db)
    creds = await asyncio.to_thread(credentials_for_user, db, user_id)
    if not creds:
        raise ClassroomAuthError(
            f"No Google authorization found for account #{user_id}. "
            "Connect Google from the Settings page."
        )
    try:
        await ensure_refreshed_for_user(db, user_id, creds)
    except GoogleAuthError as e:
        raise ClassroomAuthError(str(e)) from e

    # Only the courses scope is required to build a service / list courses.
    # ``courses.courseWork.list`` additionally needs the coursework scope; that
    # is checked separately by the functions that use it (see
    # ``_require_coursework_scope``) so a token without it can still list
    # courses instead of failing every Classroom call.
    if not has_scope(creds, CLASSROOM_COURSES_SCOPE):
        raise ClassroomAuthError(
            "Classroom permission not granted. Re-connect your Google account "
            "from the Settings page so the Classroom scopes are included "
            f"(missing: {CLASSROOM_COURSES_SCOPE})."
        )
    return build_service("classroom", "v1", creds)


def _require_coursework_scope(creds) -> None:
    """Raise ClassroomScopeError unless the coursework scope is present.

    Checked BEFORE calling ``courses.courseWork.list`` so a missing scope is
    reported as a graceful, per-feature unavailability rather than a misleading
    HTTP 403 that would mark the whole integration dead.
    """
    if not has_scope(creds, CLASSROOM_COURSEWORK_SCOPE):
        raise ClassroomScopeError(
            "Classroom coursework permission not granted. Assignment listing "
            "is unavailable."
        )


async def _call_with_retry(request_builder, max_retries: int = 3):
    import http.client
    import random

    import httplib2

    from googleapiclient.errors import HttpError

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            request = request_builder()
            return await asyncio.to_thread(request.execute)
        except HttpError as e:
            status = e.resp.status
            if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                retry_after = e.resp.get("retry-after")
                delay = float(retry_after) if retry_after else 2**attempt + random.uniform(0, 1)
                last_error = e
                await asyncio.sleep(min(delay, 30))
                continue
            if status in (401, 403):
                AuthStateStore.mark_dead(
                    reason="revoked",
                    last_error=f"Classroom API rejected the token (HTTP {status}). "
                    "Re-connect Google from the Settings page.",
                )
                raise ClassroomAuthError("Classroom authorization is invalid or expired")
            raise ClassroomUnavailableError(f"Classroom API error (HTTP {status})") from e
        except RefreshError as e:
            # Stored access token looked usable (no expiry) but the refresh
            # token is dead (e.g. revoked / "invalid_grant"). google-auth raises
            # this from inside ``request.execute()``; surface it as a graceful
            # auth error instead of an unhandled 500.
            AuthStateStore.mark_dead(
                reason="expired",
                last_error="Google authorization has expired and can't be refreshed. "
                "Re-connect Google from the Settings page.",
            )
            raise ClassroomAuthError(
                "Google authorization has expired and can't be refreshed. "
                "Re-connect Google from the Settings page."
            ) from e
        except (ConnectionError, TimeoutError, OSError, http.client.HTTPException, httplib2.HttpLib2Error) as e:
            if attempt < max_retries:
                last_error = e
                await asyncio.sleep(min(2**attempt + random.uniform(0, 1), 30))
                continue
            raise ClassroomUnavailableError(f"Could not reach the Classroom API: {e}") from e
    raise ClassroomUnavailableError(f"Classroom API request failed after retries: {last_error}")


async def list_courses(limit: int = 100, max_pages: int = None, db: Session = None, user_id: int = None) -> list[dict]:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        service = await _get_user_service(db, user_id)
        courses = []
        page_token = None
        pages_fetched = 0
        max_pages_limit = max_pages if max_pages is not None else 10
        while True:
            result = await _call_with_retry(
                lambda pt=page_token: service.courses().list(
                    pageSize=min(limit, 100), courseStates=["ACTIVE", "PROVISIONED"], pageToken=pt
                )
            )
            for c in result.get("courses", []):
                courses.append(
                    {
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "section": c.get("section"),
                        "room": c.get("room"),
                    }
                )
            page_token = result.get("nextPageToken")
            pages_fetched += 1
            if not page_token or (max_pages_limit is not None and pages_fetched >= max_pages_limit):
                break
        return courses
    finally:
        if owns_db:
            db.close()


def _due_datetime(coursework: dict):
    due = coursework.get("dueDate")
    if not due:
        return None
    try:
        d = date(due["year"], due["month"], due["day"])
        t = coursework.get("dueTime") or {}
        return datetime.combine(d, time(t.get("hours", 23), t.get("minutes", 59), 0))
    except (KeyError, TypeError, ValueError):
        return None


async def list_assignments(limit_per_course: int = 20, max_pages_per_course: int = 5, db: Session = None, user_id: int = None) -> list[dict]:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        service = await _get_user_service(db, user_id)
        creds = await asyncio.to_thread(
            credentials_for_user, db, user_id or owner_user_id(db)
        )
        _require_coursework_scope(creds)
        courses = await list_courses(100, max_pages=10, db=db, user_id=user_id)

        assignments = []
        for course in courses:
            if course["id"] not in CURRENT_SEMESTER_COURSE_IDS:
                continue
            try:
                page_token = None
                pages_fetched = 0
                while pages_fetched < max_pages_per_course:
                    result = await _call_with_retry(
                        lambda pt=page_token: service.courses().courseWork().list(
                            courseId=course["id"],
                            courseWorkStates=["PUBLISHED"],
                            pageSize=limit_per_course,
                            pageToken=pt,
                        )
                    )
                    for cw in result.get("courseWork", []):
                        assignments.append(
                            {
                                "id": cw.get("id"),
                                "course_id": course["id"],
                                "course_name": course["name"],
                                "title": cw.get("title"),
                                "description": cw.get("description"),
                                "work_type": cw.get("workType"),
                                "state": cw.get("state"),
                                "due": _due_datetime(cw).isoformat() if _due_datetime(cw) else None,
                                "max_points": cw.get("maxPoints"),
                            }
                        )
                    page_token = result.get("nextPageToken")
                    pages_fetched += 1
                    if not page_token:
                        break
            except ClassroomUnavailableError:
                continue
        return assignments
    finally:
        if owns_db:
            db.close()


SUBMITTED_STATES = {"TURNED_IN", "RETURNED"}


async def list_submissions(
    coursework_ids: set[str] | None = None, max_pages_per_course: int = 5, db: Session = None, user_id: int = None
) -> dict[str, str]:
    """Return {courseWorkId: submission_state} for the current student.

    When ``coursework_ids`` is given, only those coursework items are queried
    (much faster for background reconciliation). A coursework missing from the
    result (or with no student submission) is treated as NOT submitted.
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        service = await _get_user_service(db, user_id)
        creds = await asyncio.to_thread(
            credentials_for_user, db, user_id or owner_user_id(db)
        )
        _require_coursework_scope(creds)
        courses = await list_courses(100, max_pages=10, db=db, user_id=user_id)

        wanted = coursework_ids if coursework_ids is not None else None
        states: dict[str, str] = {}
        for course in courses:
            try:
                cw_page_token = None
                cw_pages_fetched = 0
                while cw_pages_fetched < max_pages_per_course:
                    cw_result = await _call_with_retry(
                        lambda pt=cw_page_token: service.courses().courseWork().list(
                            courseId=course["id"],
                            courseWorkStates=["PUBLISHED"],
                            pageSize=100,
                            pageToken=pt,
                        )
                    )
                    for cw in cw_result.get("courseWork", []):
                        cw_id = cw.get("id")
                        if not cw_id:
                            continue
                        if wanted is not None and cw_id not in wanted:
                            continue
                        try:
                            sub_result = await _call_with_retry(
                                lambda: service.courses().courseWork().studentSubmissions().list(
                                    courseId=course["id"],
                                    courseWorkId=cw_id,
                                    userId="me",
                                    pageSize=1,
                                )
                            )
                        except ClassroomUnavailableError:
                            continue
                        subs = sub_result.get("studentSubmissions", [])
                        if subs:
                            states[cw_id] = subs[0].get("state", "CREATED")
                    cw_page_token = cw_result.get("nextPageToken")
                    cw_pages_fetched += 1
                    if not cw_page_token:
                        break
            except ClassroomUnavailableError:
                continue
        return states
    finally:
        if owns_db:
            db.close()


async def sync_submission_status(db: Session = None, user_id: int = None) -> dict:
    """Reconcile Tasks/Assignments against the student's real Classroom
    submission state so overdue/precision is truthful."""
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    user_id = user_id or owner_user_id(db)

    class_rows = db.query(Task).filter(Task.source == "classroom")
    assignment_rows = db.query(Assignment).filter(Assignment.source == "classroom")
    if user_id:
        class_rows = class_rows.filter(Task.user_id == user_id)
        assignment_rows = assignment_rows.filter(Assignment.user_id == user_id)

    wanted_ids = {
        str(t.source_id)
        for t in class_rows.all()
    } | {
        str(a.source_id)
        for a in assignment_rows.all()
    }
    try:
        states = await list_submissions(wanted_ids, db=db, user_id=user_id)
    except ClassroomScopeError:
        # Coursework scope not granted: nothing to reconcile, but the
        # integration is still valid for courses. Do not mark it dead.
        return {
            "coursework_seen": 0,
            "tasks_completed": 0,
            "tasks_reverted": 0,
            "assignments_submitted": 0,
            "assignments_reverted": 0,
            "reason": "coursework_scope_missing",
        }
    tasks_completed = 0
    tasks_reverted = 0
    assignments_submitted = 0
    assignments_reverted = 0
    try:
        for t in class_rows.all():
            state = states.get(str(t.source_id))
            if state in SUBMITTED_STATES:
                if t.status != "completed":
                    t.status = "completed"
                    t.completed_at = datetime.now()
                    tasks_completed += 1
            elif state is None:
                if t.status == "completed":
                    t.status = "pending"
                    t.completed_at = None
                    tasks_reverted += 1

        for a in assignment_rows.all():
            state = states.get(str(a.source_id))
            if state in SUBMITTED_STATES:
                if a.status != "submitted":
                    a.status = "submitted"
                    assignments_submitted += 1
            elif state is None and a.status == "submitted":
                a.status = "todo"
                assignments_reverted += 1

        db.commit()
    finally:
        if owns_db:
            db.close()

    return {
        "coursework_seen": len(states),
        "tasks_completed": tasks_completed,
        "tasks_reverted": tasks_reverted,
        "assignments_submitted": assignments_submitted,
        "assignments_reverted": assignments_reverted,
    }


async def sync_assignments(db: Session = None, user_id: int = None) -> dict:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    user_id = user_id or owner_user_id(db)

    try:
        assignments = await list_assignments(db=db, user_id=user_id)
    except ClassroomScopeError:
        # Coursework scope not granted: no assignments to sync. Both
        # list_assignments and list_submissions need the same scope, so
        # skipping here covers submission reconciliation too.
        return {
            "assignments": 0,
            "tasks_created": 0,
            "tasks_skipped": 0,
            "tasks_submitted_skipped": 0,
            "events_created": 0,
            "events_skipped": 0,
            "assignments_created": 0,
            "assignments_updated": 0,
            "reason": "coursework_scope_missing",
        }
    submission_states = await list_submissions(
        {str(a["id"]) for a in assignments if a["id"]}, db=db, user_id=user_id
    )
    tasks_created = 0
    tasks_skipped = 0
    tasks_submitted_skipped = 0
    events_created = 0
    events_skipped = 0
    assignments_created = 0
    assignments_updated = 0
    try:
        existing_task_ids = {
            str(t.source_id) for t in db.query(Task).filter(
                Task.source == "classroom", Task.user_id == user_id
            ).all()
        }
        # For Classroom events, calendar_event_id is None (not prefilled),
        # so we dedupe by (title, start_time) tuple instead
        existing_event_keys = {
            (e.title, e.start_time) for e in db.query(Event).filter(
                Event.source == "classroom", Event.user_id == user_id
            ).all()
        }
        existing_assignment_ids = {
            str(a.source_id)
            for a in db.query(Assignment).filter(
                Assignment.source == "classroom", Assignment.user_id == user_id
            ).all()
        }

        for a in assignments:
            if not a["due"]:
                continue
            due = datetime.fromisoformat(a["due"])
            title = f"[{a['course_name']}] {a['title']}" if a["course_name"] else a["title"]
            submitted = submission_states.get(a["id"]) in SUBMITTED_STATES

            if a["id"] not in existing_task_ids:
                if submitted:
                    tasks_submitted_skipped += 1
                else:
                    db.add(
                        Task(
                            title=title,
                            description=a.get("description") or "",
                            source="classroom",
                            source_id=a["id"],
                            due_date=due,
                            priority="high",
                            status="pending",
                            user_id=user_id,
                        )
                    )
                    tasks_created += 1
            else:
                tasks_skipped += 1

            event_key = (title, due)
            if event_key not in existing_event_keys:
                db.add(
                    Event(
                        title=title,
                        description=a.get("description") or "",
                        start_time=due,
                        end_time=due.replace(hour=23, minute=59),
                        is_all_day=True,
                        source="classroom",
                        user_id=user_id,
                    )
                )
                events_created += 1
                existing_event_keys.add(event_key)
            else:
                events_skipped += 1

            if a["id"] not in existing_assignment_ids:
                db.add(
                    Assignment(
                        title=a["title"],
                        course=a.get("course_name"),
                        due_date=due,
                        status="submitted" if submitted else "todo",
                        points_max=a.get("max_points"),
                        priority="high",
                        source="classroom",
                        source_id=a["id"],
                        external_url=f"https://classroom.google.com/w/c/{a['course_id']}/t/{a['id']}",
                        user_id=user_id,
                    )
                )
                assignments_created += 1
            else:
                assignment = (
                    db.query(Assignment)
                    .filter(
                        Assignment.source == "classroom",
                        Assignment.source_id == a["id"],
                        Assignment.user_id == user_id,
                    )
                    .first()
                )
                if assignment:
                    assignment.due_date = due
                    assignment.status = "submitted" if submitted else assignment.status
                    assignment.points_max = a.get("max_points")
                    assignments_updated += 1

        db.commit()
    finally:
        if owns_db:
            db.close()

    return {
        "assignments": len(assignments),
        "tasks_created": tasks_created,
        "tasks_skipped": tasks_skipped,
        "tasks_submitted_skipped": tasks_submitted_skipped,
        "events_created": events_created,
        "events_skipped": events_skipped,
        "assignments_created": assignments_created,
        "assignments_updated": assignments_updated,
    }
