from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, Boolean, DateTime, UniqueConstraint
from app.core.database import Base
from app.models.base import TimestampMixin


class Email(Base, TimestampMixin):
    __tablename__ = "emails"
    __table_args__ = (
        # Gmail message ids are unique per mailbox (per user here, since each
        # user owns exactly one connected Google account). A global unique key
        # would let one user's message id block another user's identical id,
        # so uniqueness is scoped (user_id, message_id).
        UniqueConstraint("user_id", "message_id", name="ux_emails_user_message_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String(512), nullable=False)
    thread_id = Column(String(512))
    from_email = Column(String(256))
    from_name = Column(String(256))
    to_email = Column(String(256))
    subject = Column(String(512))
    body_text = Column(Text)
    body_html = Column(Text)
    received_at = Column(DateTime)
    category = Column(String(32), default="unknown")
    is_read = Column(Boolean, default=False)
    is_starred = Column(Boolean, default=False)
    has_deadline = Column(Boolean, default=False)
    deadline_at = Column(DateTime, nullable=True)
    deadline_description = Column(Text, nullable=True)
    suggested_reply = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    created_task_id = Column(Integer, nullable=True)
    retention_until = Column(DateTime, nullable=True)
    sensitivity = Column(String(16), nullable=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
