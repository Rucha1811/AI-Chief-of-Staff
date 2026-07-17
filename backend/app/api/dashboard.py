from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, date

from app.core.database import get_db
from app.models.email import Email
from app.models.event import Event
from app.models.task import Task
from app.models.document import Document

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(db: Session = Depends(get_db)) -> dict:
    today = date.today()
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
            Task.due_date < datetime.now(),
        ).count(),
    }

    documents_count = db.query(Document).count()

    return {
        "email_stats": email_stats,
        "today_events": today_events,
        "calendar_conflicts": conflicts,
        "task_stats": tasks,
        "documents_count": documents_count,
        "date": today.isoformat(),
    }
