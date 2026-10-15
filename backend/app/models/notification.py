from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    DateTime,
    UniqueConstraint,
)
from app.core.database import Base
from app.models.base import TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        # The fingerprint is a per-user dedup key: reminder_service looks up an
        # existing notification by (fingerprint, user_id). The previous GLOBAL
        # unique index was stricter than the application, so one user's
        # fingerprint silently blocked the identical fingerprint of another
        # user. Uniqueness is therefore scoped to (user_id, fingerprint).
        UniqueConstraint("user_id", "fingerprint", name="ux_notifications_user_fingerprint"),
    )

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(32), index=True)
    title = Column(String(512))
    message = Column(Text)
    entity_type = Column(String(32), nullable=True)
    entity_id = Column(Integer, nullable=True)
    priority = Column(String(16), default="normal")
    is_read = Column(Boolean, default=False)
    # Set after a browser push succeeds so the worker does not deliver the
    # same unread notification on every polling cycle.
    push_sent_at = Column(DateTime, nullable=True)
    # No standalone index on fingerprint: every lookup filters on
    # (user_id, fingerprint), which the composite unique index serves.
    fingerprint = Column(String(512), nullable=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
