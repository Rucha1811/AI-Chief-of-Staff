import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional

from app.core.database import get_db
from app.models.briefing import DailyBriefing
from app.models.email import Email
from app.models.event import Event
from app.models.task import Task
from app.models.user import User
from app.schemas.briefing import BriefingResponse
from app.services.ai_service import ai_service, _time_greeting
from app.services.ownership import owner_user_id
from app.utils import clean_news_summary
from app.api.deps import get_current_user

router = APIRouter(prefix="/api/briefing", tags=["briefing"])
logger = logging.getLogger(__name__)


def _fallback_briefing(meetings_data: list, emails_data: list, tasks_data: list) -> dict:
    """Keep the briefing usable when the optional local AI service is down."""
    meeting_text = (
        "; ".join(f"{item['title']} at {item['time']}" for item in meetings_data)
        or "No meetings scheduled today."
    )
    email_text = (
        "; ".join(item["subject"] for item in emails_data)
        or "No unread important emails."
    )
    task_text = (
        "; ".join(item["title"] for item in tasks_data)
        or "No pending tasks."
    )
    return {
        "greeting": _time_greeting(),
        "summary": (
            f"You have {len(meetings_data)} meeting(s), {len(emails_data)} "
            f"important unread email(s), and {len(tasks_data)} pending task(s) today."
        ),
        "meetings_highlights": meeting_text,
        "email_highlights": email_text,
        "task_highlights": task_text,
        "suggested_plan": (
            "Review important emails first, attend scheduled meetings, "
            "then work through your highest-priority pending tasks."
        ),
    }


def _serialize(briefing: DailyBriefing) -> dict:
    return {
        "greeting": _time_greeting(),
        "summary": briefing.summary,
        "meetings_count": briefing.meetings_count,
        "important_emails_count": briefing.important_emails_count,
        "pending_tasks_count": briefing.pending_tasks_count,
        "news_items": briefing.news_items or [],
        "suggested_plan": briefing.suggested_plan,
        "meetings_highlights": briefing.meetings_highlights,
        "email_highlights": briefing.email_highlights,
        "task_highlights": briefing.task_highlights,
        "full_briefing": briefing.full_briefing,
    }


async def generate_today_briefing(db: Session, user_id: Optional[int] = None) -> dict:
    today = date.today()

    day_start = datetime.combine(today, datetime.min.time())
    day_end = datetime.combine(today, datetime.max.time())
    uid = user_id or owner_user_id(db)

    meetings = db.query(Event).filter(
        Event.user_id == uid,
        Event.start_time >= day_start,
        Event.end_time <= day_end,
    ).all()

    important_emails = db.query(Email).filter(
        Email.user_id == uid,
        Email.category.in_(["important", "reply_required"]),
        Email.is_read == False,
    ).limit(10).all()

    pending_tasks = db.query(Task).filter(
        Task.user_id == uid,
        Task.status.in_(["pending", "in_progress"]),
    ).order_by(Task.priority.desc()).limit(10).all()

    meetings_data = [
        {"title": m.title, "time": m.start_time.strftime("%H:%M"), "duration": f"{(m.end_time - m.start_time).seconds // 60}min"}
        for m in meetings
    ]
    emails_data = [
        {"subject": e.subject, "from": e.from_name or e.from_email}
        for e in important_emails
    ]
    tasks_data = [
        {"title": t.title, "priority": t.priority, "due": t.due_date.strftime("%Y-%m-%d") if t.due_date else None}
        for t in pending_tasks
    ]

    async def get_news():
        from app.services.news_service import fetch_news
        try:
            items = await fetch_news("technology", limit=3)
            return [
                {
                    "title": i["title"],
                    "summary": clean_news_summary(i.get("summary") or ""),
                }
                for i in items
            ]
        except Exception:
            return []

    news_items = await get_news()

    try:
        result = await ai_service.generate_daily_briefing(
            meetings_data, emails_data, tasks_data, news_items
        )
    except Exception as exc:
        logger.warning("AI briefing unavailable; using data-only fallback: %s", exc)
        result = _fallback_briefing(meetings_data, emails_data, tasks_data)

    import json
    def _safe_text(v):
        return json.dumps(v) if isinstance(v, (dict, list)) else (str(v) if v is not None else "")

    briefing = DailyBriefing(
        date=datetime.now(),
        summary=_safe_text(result.get("summary", "")),
        meetings_count=len(meetings),
        important_emails_count=len(important_emails),
        pending_tasks_count=len(pending_tasks),
        news_items=news_items,
        suggested_plan=_safe_text(result.get("suggested_plan", "")),
        meetings_highlights=_safe_text(result.get("meetings_highlights", "")),
        email_highlights=_safe_text(result.get("email_highlights", "")),
        task_highlights=_safe_text(result.get("task_highlights", "")),
        full_briefing=_safe_text(result.get("full_briefing", result.get("summary", ""))),
        user_id=user_id or owner_user_id(db),
    )
    db.add(briefing)
    db.commit()

    return {
        "greeting": result.get("greeting", _time_greeting()),
        "summary": result.get("summary", ""),
        "meetings_count": len(meetings),
        "important_emails_count": len(important_emails),
        "pending_tasks_count": len(pending_tasks),
        "news_items": news_items,
        "suggested_plan": result.get("suggested_plan", ""),
        "meetings_highlights": result.get("meetings_highlights", ""),
        "email_highlights": result.get("email_highlights", ""),
        "task_highlights": result.get("task_highlights", ""),
        "full_briefing": briefing.full_briefing,
    }


@router.get("/today")
async def get_today_briefing(
    refresh: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    today = date.today()
    existing = db.query(DailyBriefing).filter(
        DailyBriefing.user_id == user.id,
        DailyBriefing.date >= datetime.combine(today, datetime.min.time()),
        DailyBriefing.date <= datetime.combine(today, datetime.max.time()),
    ).first()

    if existing and not refresh:
        return _serialize(existing)

    if existing:
        db.delete(existing)
        db.commit()

    return await generate_today_briefing(db, user_id=user.id)
