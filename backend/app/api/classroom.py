import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.api.deps import get_current_user
from app.services import classroom_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/classroom", tags=["classroom"])

# Client-safe messages. The real exception (Google/provider detail) is logged
# server-side only; clients get a stable, non-sensitive reason.
_AUTH_MSG = "Classroom authorization required. Reconnect Google in Settings."
_UNAVAILABLE_MSG = "Classroom is temporarily unavailable. Please try again later."
_SCOPE_MSG = "Classroom assignments need the coursework permission. Reconnect Google and grant Classroom coursework access."


@router.get("/status")
async def classroom_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    try:
        await classroom_service._get_user_service(db, user_id=user.id)
        return {"connected": True, "reason": "ok"}
    except classroom_service.ClassroomAuthError:
        return {"connected": False, "reason": "authorization_required"}
    except Exception as e:
        logger.warning("classroom status failed for user %s: %s", user.id, e)
        return {"connected": False, "reason": "unavailable"}


@router.get("/courses")
async def list_courses(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    try:
        return await classroom_service.list_courses(db=db, user_id=user.id)
    except classroom_service.ClassroomAuthError as e:
        logger.warning("classroom auth failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_AUTH_MSG)
    except classroom_service.ClassroomUnavailableError as e:
        logger.warning("classroom unavailable for user %s: %s", user.id, e)
        raise HTTPException(status_code=502, detail=_UNAVAILABLE_MSG)


@router.get("/assignments")
async def list_assignments(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    try:
        return await classroom_service.list_assignments(db=db, user_id=user.id)
    except classroom_service.ClassroomAuthError as e:
        logger.warning("classroom auth failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_AUTH_MSG)
    except classroom_service.ClassroomScopeError as e:
        logger.warning("classroom scope failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_SCOPE_MSG)
    except classroom_service.ClassroomUnavailableError as e:
        logger.warning("classroom unavailable for user %s: %s", user.id, e)
        raise HTTPException(status_code=502, detail=_UNAVAILABLE_MSG)


@router.post("/sync")
async def sync_classroom(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    try:
        result = await classroom_service.sync_assignments(db=db, user_id=user.id)
        result["submissions"] = await classroom_service.sync_submission_status(
            db=db, user_id=user.id
        )
        return result
    except classroom_service.ClassroomAuthError as e:
        logger.warning("classroom auth failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_AUTH_MSG)
    except classroom_service.ClassroomScopeError as e:
        logger.warning("classroom scope failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_SCOPE_MSG)
    except classroom_service.ClassroomUnavailableError as e:
        logger.warning("classroom unavailable for user %s: %s", user.id, e)
        raise HTTPException(status_code=502, detail=_UNAVAILABLE_MSG)


@router.post("/sync-submissions")
async def sync_submissions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    try:
        return await classroom_service.sync_submission_status(db=db, user_id=user.id)
    except classroom_service.ClassroomAuthError as e:
        logger.warning("classroom auth failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_AUTH_MSG)
    except classroom_service.ClassroomScopeError as e:
        logger.warning("classroom scope failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_SCOPE_MSG)
    except classroom_service.ClassroomUnavailableError as e:
        logger.warning("classroom unavailable for user %s: %s", user.id, e)
        raise HTTPException(status_code=502, detail=_UNAVAILABLE_MSG)
