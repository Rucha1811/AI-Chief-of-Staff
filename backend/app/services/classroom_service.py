import asyncio
import logging
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.assignment import Assignment
from app.models.event import Event
from app.models.task import Task
from app.services.google_auth import (
    GoogleAuthError,
    build_service,
    ensure_refreshed,
    has_scope,
    load_credentials,
)
from app.services.auth_state import AuthStateStore

logger = logging.getLogger(__name__)

CLASSROOM_COURSES_SCOPE = "https://www.googleapis.com/auth/classroom.courses.readonly"
CLASSROOM_COURSEWORK_SCOPE = "https://www.googleapis.com/auth/classroom.coursework.me.readonly"

# Tracked courses are org-scoped, deployment-specific data. They must be set
# via CLASSROOM_COURSE_IDS in .env; no course IDs belong in code. When empty,
# all courses from the connected Classroom account are tracked.
CURRENT_SEMESTER_COURSE_IDS = set(settings.classroom_course_ids)


class ClassroomAuthError(RuntimeError):
    pass


class ClassroomUnavailableError(RuntimeError):
    pass


async def _get_service():
    creds = load_credentials()
    if not creds:
        raise ClassroomAuthError(
            "No Google authorization found. Authenticate via /api/emails/re-auth or run: python test_gmail.py"
        )
    try:
        await ensure_refreshed(creds)
    except GoogleAuthError as e:
        raise ClassroomAuthError(str(e)) from e

    missing = []
    for scope in (CLASSROOM_COURSES_SCOPE, CLASSROOM_COURSEWORK_SCOPE):
        if scope == CLASSROOM_COURSEWORK_SCOPE and has_scope(
            creds, "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly"
        ):
            continue
        if not has_scope(creds, scope):
            missing.append(scope)
    if missing:
        raise ClassroomAuthError(
            "Classroom permission not granted yet. Re-authorize your Google account "
            "via /api/emails/re-auth so the Classroom scope is included."
        )
    return build_service("classroom", "v1", creds)


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
                    "Re-authorize via /api/emails/re-auth.",
                )
                raise ClassroomAuthError("Classroom authorization is invalid or expired")
            raise ClassroomUnavailableError(f"Classroom API error (HTTP {status})") from e
        except (ConnectionError, TimeoutError, OSError, http.client.HTTPException, httplib2.HttpLib2Error) as e:
            if attempt < max_retries:
                last_error = e
                await asyncio.sleep(min(2**attempt + random.uniform(0, 1), 30))
                continue
            raise ClassroomUnavailableError(f"Could not reach the Classroom API: {e}") from e
    raise ClassroomUnavailableError(f"Classroom API request failed after retries: {last_error}")


async def list_courses(limit: int = 100) -> list[dict]:
    service = await _get_service()
    result = await _call_with_retry(
        lambda: service.courses().list(
            pageSize=limit, courseStates=["ACTIVE", "PROVISIONED"]
        )
    )
    courses = []
    for c in result.get("courses", []):
        courses.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "section": c.get("section"),
                "room": c.get("room"),
            }
        )
    return courses


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


async def list_assignments(limit_per_course: int = 20) -> list[dict]:
    service = await _get_service()
    courses = await list_courses(100)

    assignments = []
    for course in courses:
        if course["id"] not in CURRENT_SEMESTER_COURSE_IDS:
            continue
        try:
            result = await _call_with_retry(
                lambda: service.courses().courseWork().list(
                    courseId=course["id"],
                    courseWorkStates=["PUBLISHED"],
                    pageSize=limit_per_course,
                )
            )
        except ClassroomUnavailableError:
            continue
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
    return assignments


SUBMITTED_STATES = {"TURNED_IN", "RETURNED"}


async def list_submissions(coursework_ids: set[str] | None = None) -> dict[str, str]:
    """Return {courseWorkId: submission_state} for the current student.

    When ``coursework_ids`` is given, only those coursework items are queried
    (much faster for background reconciliation). A coursework missing from the
    result (or with no student submission) is treated as NOT submitted.
    """
    service = await _get_service()
    courses = await list_courses(100)

    wanted = coursework_ids if coursework_ids is not None else None
    states: dict[str, str] = {}
    for course in courses:
        try:
            cw_result = await _call_with_retry(
                lambda: service.courses().courseWork().list(
                    courseId=course["id"],
                    courseWorkStates=["PUBLISHED"],
                    pageSize=100,
                )
            )
        except ClassroomUnavailableError:
            continue
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
    return states


async def sync_submission_status(db: Session = None) -> dict:
    """Reconcile Tasks/Assignments against the student's real Classroom
    submission state so overdue/precision is truthful."""
    owns_db = db is None
    if owns_db:
        db = SessionLocal()

    wanted_ids = {
        str(t.source_id)
        for t in db.query(Task).filter(Task.source == "classroom").all()
    } | {
        str(a.source_id)
        for a in db.query(Assignment).filter(Assignment.source == "classroom").all()
    }
    states = await list_submissions(wanted_ids)
    tasks_completed = 0
    tasks_reverted = 0
    assignments_submitted = 0
    assignments_reverted = 0
    try:
        for t in db.query(Task).filter(Task.source == "classroom").all():
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

        for a in db.query(Assignment).filter(Assignment.source == "classroom").all():
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


async def sync_assignments(db: Session = None) -> dict:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()

    assignments = await list_assignments()
    submission_states = await list_submissions(
        {str(a["id"]) for a in assignments if a["id"]}
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
            t.source_id for t in db.query(Task).filter(Task.source == "classroom").all()
        }
        existing_event_ids = {
            e.calendar_event_id for e in db.query(Event).filter(Event.source == "classroom").all()
        }
        existing_assignment_ids = {
            a.source_id
            for a in db.query(Assignment).filter(Assignment.source == "classroom").all()
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
                        )
                    )
                    tasks_created += 1
            else:
                tasks_skipped += 1

            if a["id"] not in existing_event_ids:
                db.add(
                    Event(
                        calendar_event_id=a["id"],
                        title=title,
                        description=a.get("description") or "",
                        start_time=due,
                        end_time=due.replace(hour=23, minute=59),
                        is_all_day=True,
                        source="classroom",
                    )
                )
                events_created += 1
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
                    )
                )
                assignments_created += 1
            else:
                assignment = (
                    db.query(Assignment)
                    .filter(
                        Assignment.source == "classroom",
                        Assignment.source_id == a["id"],
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
