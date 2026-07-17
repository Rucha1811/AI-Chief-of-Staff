from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, datetime

from app.core.database import get_db
from app.models.briefing import DailyBriefing
from app.models.email import Email
from app.models.event import Event
from app.models.task import Task
from app.schemas.briefing import BriefingResponse
from app.services.ai_service import ai_service

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


@router.get("/today")
async def get_today_briefing(db: Session = Depends(get_db)) -> dict:
    today = date.today()
    existing = db.query(DailyBriefing).filter(
        DailyBriefing.date >= datetime.combine(today, datetime.min.time()),
        DailyBriefing.date <= datetime.combine(today, datetime.max.time()),
    ).first()

    if existing:
        return {
            "greeting": "Good Morning!",
            "summary": existing.summary,
            "meetings_count": existing.meetings_count,
            "important_emails_count": existing.important_emails_count,
            "pending_tasks_count": existing.pending_tasks_count,
            "news_items": existing.news_items or [],
            "suggested_plan": existing.suggested_plan,
            "full_briefing": existing.full_briefing,
        }

    day_start = datetime.combine(today, datetime.min.time())
    day_end = datetime.combine(today, datetime.max.time())

    meetings = db.query(Event).filter(
        Event.start_time >= day_start,
        Event.end_time <= day_end,
    ).all()

    important_emails = db.query(Email).filter(
        Email.category.in_(["important", "reply_required"]),
        Email.is_read == False,
    ).limit(10).all()

    pending_tasks = db.query(Task).filter(
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
        {"title": t.title, "priority": t.priority.value, "due": t.due_date.strftime("%Y-%m-%d") if t.due_date else None}
        for t in pending_tasks
    ]

    async def get_news():
        try:
            import httpx
            resp = await httpx.AsyncClient().get(
                "https://newsapi.org/v2/top-headlines",
                params={"category": "technology", "country": "us", "pageSize": 3},
                timeout=10,
            )
            articles = resp.json().get("articles", [])
            return [
                {"title": a.get("title", ""), "summary": a.get("description", "")}
                for a in articles[:3]
            ]
        except Exception:
            return []

    news_items = await get_news()

    result = await ai_service.generate_daily_briefing(meetings_data, emails_data, tasks_data, news_items)

    briefing = DailyBriefing(
        date=datetime.now(),
        summary=result.get("summary", ""),
        meetings_count=len(meetings),
        important_emails_count=len(important_emails),
        pending_tasks_count=len(pending_tasks),
        news_items=news_items,
        suggested_plan=result.get("suggested_plan", ""),
        full_briefing=result.get("full_briefing", result.get("summary", "")),
    )
    db.add(briefing)
    db.commit()

    return {
        "greeting": result.get("greeting", "Good Morning!"),
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
