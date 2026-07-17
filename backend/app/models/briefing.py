from sqlalchemy import Column, Integer, Text, DateTime, JSON
from app.core.database import Base
from app.models.base import TimestampMixin


class DailyBriefing(Base, TimestampMixin):
    __tablename__ = "daily_briefings"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, unique=True, index=True)
    summary = Column(Text)
    meetings_count = Column(Integer, default=0)
    important_emails_count = Column(Integer, default=0)
    pending_tasks_count = Column(Integer, default=0)
    news_items = Column(JSON, nullable=True)
    suggested_plan = Column(Text, nullable=True)
    full_briefing = Column(Text)
