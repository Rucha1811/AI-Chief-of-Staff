from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import teams_service

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("/status")
async def teams_status() -> dict:
    return await teams_service.status()


@router.post("/connect")
async def teams_connect() -> dict:
    try:
        return await teams_service.start_device_flow()
    except teams_service.TeamsAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/connect/poll")
async def teams_connect_poll() -> dict:
    try:
        return await teams_service.poll_device_flow()
    except teams_service.TeamsAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sync")
async def teams_sync(db: Session = Depends(get_db)) -> dict:
    try:
        return await teams_service.sync_events(db=db)
    except teams_service.TeamsAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except teams_service.TeamsUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))
