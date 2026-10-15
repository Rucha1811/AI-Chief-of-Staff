from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Boolean
from app.core.database import Base
from app.models.base import TimestampMixin


class TimetableEntry(Base, TimestampMixin):
    __tablename__ = "timetable_entries"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(512), nullable=False)
    location = Column(String(256), nullable=True)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    source = Column(String(64), default="college")
    active = Column(Boolean, default=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
