from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class BriefingResponse(BaseModel):
    id: int
    date: datetime
    summary: str
    meetings_count: int
    important_emails_count: int
    pending_tasks_count: int
    news_items: Optional[list] = None
    suggested_plan: Optional[str] = None
    full_briefing: str
    created_at: datetime

    class Config:
        from_attributes = True
