from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.core.database import Base
from app.models.base import TimestampMixin


class EGovAttendance(Base, TimestampMixin):
    """One subject-level attendance row from the CHARUSAT e-Governance portal.

    This table holds a *snapshot*: a successful sync or import replaces every
    row for that user, so the rows always represent the last known-good
    attendance. When a later refresh fails the existing rows are deliberately
    left untouched and surfaced with their ``synced_at`` so the UI can label
    them stale instead of showing an empty screen.

    Only data the source actually published is stored; a field the portal did
    not provide stays NULL rather than being filled with a fabricated zero.
    ``term`` / ``subject_code``/``subject_name`` default to "" rather than NULL
    so the natural-key unique constraint is enforced deterministically
    (PostgreSQL treats NULLs as distinct in a UNIQUE constraint).
    """

    __tablename__ = "egov_attendance"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "term",
            "subject_code",
            "subject_name",
            name="ux_egov_attendance_subject",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    term = Column(String(64), nullable=False, default="", server_default="")
    subject_code = Column(String(64), nullable=False, default="", server_default="")
    subject_name = Column(String(256), nullable=False, default="", server_default="")
    classes_held = Column(Integer, nullable=True)
    classes_attended = Column(Integer, nullable=True)
    percentage = Column(Float, nullable=True)
    # Timestamp the *source* reported, when it exposes one. Never guessed.
    source_updated_at = Column(DateTime, nullable=True)
    # When this snapshot was last refreshed from the portal.
    synced_at = Column(DateTime, nullable=True)
    # Provenance: the attendance URL used for a session sync, or "import".
    source_reference = Column(String(512), nullable=True)
