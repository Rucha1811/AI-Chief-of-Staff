from sqlalchemy import (
    BigInteger,
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


class GoogleAuthState(Base, TimestampMixin):
    __tablename__ = "google_auth_state"
    __table_args__ = (
        # Exactly one Google authorization-health row per user (mirrors the
        # ux_google_auth_state_user index from migration 0005). Declared here
        # so create_all matches Alembic and AuthStateStore's get-or-create
        # IntegrityError fallback has a constraint to rely on.
        UniqueConstraint("user_id", name="ux_google_auth_state_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(256), nullable=True)
    state = Column(String(32), default="unknown")
    reason = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    refresh_attempts = Column(Integer, default=0)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
