from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(512))
    description = Column(Text, nullable=True)
    priority = Column(String(16), default="medium")
    status = Column(String(16), default="pending")
    source = Column(String(32), default="user")
    source_id = Column(Integer, nullable=True)
    due_date = Column(DateTime, nullable=True)
    estimated_minutes = Column(Integer, nullable=True)
    is_recurring = Column(Boolean, default=False)
    recurrence_rule = Column(String(256), nullable=True)
    completed_at = Column(DateTime, nullable=True)
