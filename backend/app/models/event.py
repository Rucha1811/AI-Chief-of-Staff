from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    UniqueConstraint,
)
from app.core.database import Base
from app.models.base import TimestampMixin


class Event(Base, TimestampMixin):
    __tablename__ = "events"
    __table_args__ = (
        # Google/Teams events carry an external event id that is only unique
        # within one user's account (classmates share coursework ids), so the
        # uniqueness must be scoped per user.
        UniqueConstraint("user_id", "calendar_event_id", name="ux_events_user_calendar_event_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    calendar_event_id = Column(String(512), nullable=True)
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
    recurrence_rule = Column(String(32), nullable=True)
    recurrence_until = Column(DateTime, nullable=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
