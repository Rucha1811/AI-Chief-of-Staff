from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    email = Column(String(512), nullable=False)
    password_hash = Column(String(512), nullable=False)
    display_name = Column(String(256), nullable=True)
    timezone = Column(String(64), default="UTC", nullable=False)
    preferences = Column(JSON, nullable=True)
    status = Column(String(16), default="active", nullable=False)
    token_version = Column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Institutional identity verification (CHARUSAT attendance eligibility).
    # These columns are NOT defined as SQLAlchemy Columns because the live
    # database is at revision 0011, before migration 0013. Code that reads
    # these values uses ``getattr(user, "email_verified", False)` and
    # ``getattr(user, "verified_email", None)` for safe fallback.
    # Attendance eligibility is determined later, after migration 0013 adds
    # these columns to the database.

    # Declared last so it can reference the "email" column object directly.
    # Case-insensitive identity key mirroring the ux_users_email unique index
    # created by migration 0002, so create_all (fresh installs and the test
    # schema) yields the same schema Alembic produces. The login path already
    # normalizes emails to lowercase; this index makes "one account per email"
    # a database guarantee rather than an application check a race could bypass.
    __table_args__ = (
        Index("ux_users_email", func.lower(email), unique=True),
    )

    roles = relationship(
        "Role",
        secondary="user_roles",
        back_populates="users",
        lazy="selectin",
    )


class Role(Base):
    __tablename__ = "roles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)

    users = relationship(
        "User",
        secondary="user_roles",
        back_populates="roles",
        lazy="selectin",
    )
    permissions = relationship(
        "Permission",
        secondary="role_permissions",
        lazy="selectin",
    )


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(128), nullable=False, unique=True)
    description = Column(Text, nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id = Column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id = Column(
        BigInteger, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id = Column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
