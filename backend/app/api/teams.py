import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.api.deps import get_current_user
from app.services import teams_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams", tags=["teams"])

# Client-safe messages; real provider/config detail stays in server logs.
_AUTH_MSG = "Teams authorization failed. Check Microsoft configuration or reconnect."
_UNAVAILABLE_MSG = "Teams is temporarily unavailable. Please try again later."


@router.get("/status")
async def teams_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return await teams_service.status(db, user.id)


@router.post("/connect")
async def teams_connect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await teams_service.start_device_flow(db, user.id)
    except teams_service.TeamsAuthError as e:
        logger.warning("teams connect failed for user %s: %s", user.id, e)
        raise HTTPException(status_code=400, detail=_AUTH_MSG)


@router.post("/connect/poll")
async def teams_connect_poll(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await teams_service.poll_device_flow(db, user.id)
    except teams_service.TeamsAuthError as e:
        logger.warning("teams connect poll failed for user %s: %s", user.id, e)
        raise HTTPException(status_code=400, detail=_AUTH_MSG)


@router.post("/sync")
async def teams_sync(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await teams_service.sync_events(db=db, user_id=user.id)
    except teams_service.TeamsAuthError as e:
        logger.warning("teams auth failure for user %s: %s", user.id, e)
        raise HTTPException(status_code=503, detail=_AUTH_MSG)
    except teams_service.TeamsUnavailableError as e:
        logger.warning("teams unavailable for user %s: %s", user.id, e)
        raise HTTPException(status_code=502, detail=_UNAVAILABLE_MSG)
