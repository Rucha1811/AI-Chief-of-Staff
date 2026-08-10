from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta

from app.core.database import get_db
from app.models.email import Email
from app.models.event import Event
from app.models.task import Task
from app.models.assignment import Assignment
from app.models.document import Document
from app.services.gmail_service import get_account_email
from app.services.auth_state import AuthStateStore

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(db: Session = Depends(get_db)) -> dict:
    today = date.today()
    now = datetime.now()
    day_start = datetime.combine(today, datetime.min.time())
    day_end = datetime.combine(today, datetime.max.time())

    email_stats = {
        "total": db.query(Email).count(),
        "unread": db.query(Email).filter(Email.is_read == False).count(),
        "reply_required": db.query(Email).filter(Email.category == "reply_required").count(),
        "important": db.query(Email).filter(Email.category == "important").count(),
        "with_deadlines": db.query(Email).filter(Email.has_deadline == True).count(),
    }

    today_events = db.query(Event).filter(
        Event.start_time >= day_start,
        Event.end_time <= day_end,
    ).count()

    conflicts = db.query(Event).filter(
        Event.start_time >= day_start,
        Event.end_time <= day_end,
        Event.is_conflict == True,
    ).count()

    tasks = {
        "total": db.query(Task).count(),
        "pending": db.query(Task).filter(Task.status == "pending").count(),
        "in_progress": db.query(Task).filter(Task.status == "in_progress").count(),
        "completed": db.query(Task).filter(Task.status == "completed").count(),
        "overdue": db.query(Task).filter(
            Task.status.in_(["pending", "in_progress"]),
            Task.due_date < now,
        ).count(),
    }

    documents_count = db.query(Document).count()

    deadlines = []
    for t in db.query(Task).filter(
        Task.status.in_(["pending", "in_progress"]),
        Task.due_date.isnot(None),
    ).all():
        deadlines.append(
            {
                "title": t.title,
                "kind": "Task",
                "course": None,
                "due_at": t.due_date.isoformat(),
                "overdue": t.due_date < now,
            }
        )
    for a in db.query(Assignment).filter(
        Assignment.status.in_(["todo", "in_progress"]),
        Assignment.due_date.isnot(None),
    ).all():
        deadlines.append(
            {
                "title": a.title,
                "kind": "Assignment",
                "course": a.course,
                "due_at": a.due_date.isoformat(),
                "overdue": a.due_date < now,
            }
        )
    for e in db.query(Email).filter(
        Email.has_deadline.is_(True),
        Email.deadline_at.isnot(None),
    ).all():
        deadlines.append(
            {
                "title": e.subject or "Email",
                "kind": "Email deadline",
                "course": None,
                "due_at": e.deadline_at.isoformat(),
                "overdue": e.deadline_at < now,
            }
        )
    deadlines.sort(key=lambda d: d["due_at"])

    week_from_now = now + timedelta(days=7)
    upcoming = [d for d in deadlines if d["due_at"] >= now.isoformat()]
    overdue_items = [d for d in deadlines if d["overdue"]]
    deadline_count = len(upcoming)
    deadline_this_week = len(
        [d for d in upcoming if d["due_at"] <= week_from_now.isoformat()]
    )

    important_emails = [
        {
            "id": e.id,
            "subject": e.subject or "(no subject)",
            "sender": e.from_name or e.from_email,
            "category": e.category,
        }
        for e in db.query(Email)
        .filter(
            Email.category.in_(["important", "reply_required"]),
            Email.is_read == False,
        )
        .limit(5)
        .all()
    ]

    return {
        "email_stats": email_stats,
        "today_events": today_events,
        "calendar_conflicts": conflicts,
        "task_stats": tasks,
        "documents_count": documents_count,
        "upcoming_deadlines": deadlines[:5],
        "overdue_deadline_count": len(overdue_items),
        "deadline_count": deadline_count,
        "deadline_this_week": deadline_this_week,
        "important_emails": important_emails,
        "student_email": await get_account_email(),
        "google_auth": AuthStateStore.get_state(),
        "date": today.isoformat(),
    }
