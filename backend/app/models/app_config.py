"""Internal key/value store for one-shot, non-reversible security markers.

P24B-06. Only writes security *state* live here (e.g. "the first-owner
bootstrap has been consumed"); nothing readable is ever stored - no secrets, no
tokens, no user data. The table is never exposed by an API route.

A durable marker is what makes one-shot operations one-shot: an in-process flag
would be lost on restart and would let a second owner be created later, and an
"is the users table empty?" check alone would re-open the door if the owner row
were ever removed.
"""

from sqlalchemy import Column, DateTime, String, Text, func

from app.core.database import Base
from app.models.base import TimestampMixin


class AppConfig(Base, TimestampMixin):
    __tablename__ = "app_config"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    claimed_at = Column(DateTime, server_default=func.now(), nullable=False)
