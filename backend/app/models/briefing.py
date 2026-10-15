from sqlalchemy import BigInteger, Column, ForeignKey, Integer, Text, DateTime, JSON, UniqueConstraint
from app.core.database import Base
from app.models.base import TimestampMixin


class DailyBriefing(Base, TimestampMixin):
    __tablename__ = "daily_briefings"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="ux_daily_briefings_user_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime)
    summary = Column(Text)
    meetings_count = Column(Integer, default=0)
    important_emails_count = Column(Integer, default=0)
    pending_tasks_count = Column(Integer, default=0)
    news_items = Column(JSON, nullable=True)
    suggested_plan = Column(Text, nullable=True)
    meetings_highlights = Column(Text, nullable=True)
    email_highlights = Column(Text, nullable=True)
    task_highlights = Column(Text, nullable=True)
    full_briefing = Column(Text)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
