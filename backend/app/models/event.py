from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    calendar_event_id = Column(String(512), unique=True, nullable=True)
    title = Column(String(512))
    description = Column(Text, nullable=True)
    location = Column(String(256), nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    is_all_day = Column(Boolean, default=False)
    is_conflict = Column(Boolean, default=False)
    conflict_description = Column(Text, nullable=True)
    suggested_action = Column(Text, nullable=True)
    source = Column(String(64), default="manual")
