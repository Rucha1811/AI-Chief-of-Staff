from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from app.core.database import Base
from app.models.base import TimestampMixin


class UserIntegration(Base, TimestampMixin):
    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="ux_user_integrations_user_provider"),
    )

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = Column(String(64), nullable=False)
    account_id = Column(String(512), nullable=True)
    status = Column(String(16), default="active", nullable=False)
    refresh_token_encrypted = Column(Text, nullable=True)
    access_token_encrypted = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    meta = Column(JSON, nullable=True)