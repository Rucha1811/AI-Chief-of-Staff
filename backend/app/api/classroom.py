from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import classroom_service

router = APIRouter(prefix="/api/classroom", tags=["classroom"])


@router.get("/status")
async def classroom_status() -> dict:
    try:
        await classroom_service._get_service()
        return {"connected": True, "reason": "ok"}
    except classroom_service.ClassroomAuthError as e:
        return {"connected": False, "reason": str(e)}
    except Exception as e:
        return {"connected": False, "reason": str(e)}


@router.get("/courses")
async def list_courses() -> list[dict]:
    try:
        return await classroom_service.list_courses()
    except classroom_service.ClassroomAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except classroom_service.ClassroomUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/assignments")
async def list_assignments() -> list[dict]:
    try:
        return await classroom_service.list_assignments()
    except classroom_service.ClassroomAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except classroom_service.ClassroomUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/sync")
async def sync_classroom(db: Session = Depends(get_db)) -> dict:
    try:
        result = await classroom_service.sync_assignments(db=db)
        result["submissions"] = await classroom_service.sync_submission_status(db=db)
        return result
    except classroom_service.ClassroomAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except classroom_service.ClassroomUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/sync-submissions")
async def sync_submissions() -> dict:
    try:
        return await classroom_service.sync_submission_status()
    except classroom_service.ClassroomAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except classroom_service.ClassroomUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))
