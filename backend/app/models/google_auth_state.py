from sqlalchemy import Column, Integer, String, Text, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class GoogleAuthState(Base, TimestampMixin):
    __tablename__ = "google_auth_state"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(256), nullable=True)
    state = Column(String(32), default="unknown")
    reason = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    refresh_attempts = Column(Integer, default=0)
