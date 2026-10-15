"""CHARUSAT e-Governance attendance API (read-only, per-user, student-only).

Access rules
------------
* The caller must be authenticated (normal user session).
* The caller must be an *eligible* CHARUSAT institutional identity, proven by an
  identity provider's verified-email claim (see ``charusat_identity``). A
  user-editable ``users.email`` string is never sufficient.
* Every query is scoped to the authenticated ``user.id``. A student cannot name
  another student - there is no identifier parameter at all - so one account can
  never read or refresh another's attendance.

The portal session cookie is accepted only to be stored encrypted by the
service, and is never echoed back, logged, or returned in any response.

These endpoints are synchronous on purpose: each one may perform a bounded
network read from the university portal, and FastAPI runs ``def`` handlers in a
worker thread so the event loop is never blocked.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import charusat_identity, egov_service
from app.services.egov_service import (
    EGovError,
    EGovParseError,
    EGovSessionExpired,
    EGovUnavailable,
)

router = APIRouter(prefix="/api/attendance/egov", tags=["egov-attendance"])


class SessionConnectRequest(BaseModel):
    # The cookie value the student copied from their own signed-in browser
    # session. Stored encrypted; never returned. There is deliberately NO
    # student-identifier field: attendance is always the caller's own.
    session_cookie: str
    # The exact attendance page URL the student is viewing while signed in.
    attendance_url: str
    term: Optional[str] = None


class ImportRequest(BaseModel):
    # Saved HTML of the attendance page (no credentials involved).
    html: str
    term: Optional[str] = None


def _translate(exc: EGovError) -> HTTPException:
    """Map a domain failure to an actionable HTTP status (never leaks a secret)."""
    if isinstance(exc, EGovSessionExpired):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, EGovUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, EGovParseError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _require_eligible(user: User) -> dict:
    """403 unless the caller is a verified CHARUSAT institutional identity."""
    info = charusat_identity.eligibility(user)
    if not info["eligible"]:
        raise HTTPException(
            status_code=403, detail=charusat_identity.public_eligibility(user)
        )
    return info


def _ineligible_payload(user: User) -> dict:
    """A well-formed status object for a caller who may not use the feature.

    No records and no connection are exposed, so a non-institutional account can
    never be shown personal CHARUSAT attendance.
    """
    info = charusat_identity.public_eligibility(user)
    return {
        "eligible": False,
        "eligibility_reason": info["reason"],
        "account": info["account"],
        "domain": info["domain"],
        "connected": False,
        "state": "not_eligible",
        "method": None,
        "status": None,
        "last_synced_at": None,
        "stale": False,
        "last_error": None,
        "record_count": 0,
        "overall_percentage": None,
        "holdings": {"held": 0, "attended": 0},
        "student_verified": False,
        "records": [],
        "analysis": None,
    }


def _status_for(db: Session, user: User) -> dict:
    """Connection status + dashboard, labelled with the caller's masked identity."""
    body = egov_service.status_for_user(db, user.id)
    info = charusat_identity.public_eligibility(user)
    body["eligible"] = True
    body["eligibility_reason"] = "ok"
    body["domain"] = info["domain"]
    # The connection label comes from the caller's own verified address, never
    # from a request field.
    body["account"] = info["account"] or body.get("account")
    return body


@router.get("")
def get_egov_attendance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Eligibility, connection state and the last known-good snapshot."""
    if not charusat_identity.eligibility(user)["eligible"]:
        return _ineligible_payload(user)
    return _status_for(db, user)


@router.post("/session")
def connect_session(
    payload: SessionConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Store an encrypted session cookie and immediately try one read-only sync."""
    info = _require_eligible(user)
    try:
        egov_service.save_session(
            db,
            user.id,
            session_cookie=payload.session_cookie,
            attendance_url=payload.attendance_url,
            # Label the connection with the verified identity, never user input.
            account_id=info.get("verified_email"),
        )
        egov_service.sync_for_user(db, user.id)
    except EGovError as exc:
        raise _translate(exc) from exc
    return _status_for(db, user)


@router.post("/import")
def import_attendance_page(
    payload: ImportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Parse and store a saved attendance page (no credentials involved)."""
    _require_eligible(user)
    try:
        result = egov_service.import_html(
            db, user.id, html=payload.html, term=payload.term
        )
    except EGovError as exc:
        raise _translate(exc) from exc
    body = _status_for(db, user)
    body["imported"] = len(result.rows)
    body["skipped"] = result.skipped
    return body


@router.post("/refresh")
def refresh_egov_attendance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Re-read the attendance page with the stored session cookie."""
    _require_eligible(user)
    try:
        egov_service.sync_for_user(db, user.id)
    except EGovError as exc:
        raise _translate(exc) from exc
    return _status_for(db, user)


@router.delete("")
def disconnect_egov_attendance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Remove this user's e-Governance connection and snapshot.

    Deliberately not eligibility-gated: an account that has become ineligible
    must still be able to clean up its own connection. Only the
    ``charusat_egov`` integration row is touched, so a Google connection (and
    its credentials) is unaffected.
    """
    removed = egov_service.disconnect(db, user.id)
    return {"ok": True, "disconnected": removed}
