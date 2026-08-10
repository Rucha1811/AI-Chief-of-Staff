from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.attendance import Attendance
from app.schemas.attendance import AttendanceCreate, AttendanceResponse, AttendanceStats

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


def _serialize(a: Attendance) -> dict:
    return {
        "id": a.id,
        "course": a.course,
        "date": a.date.isoformat() if a.date else None,
        "status": a.status,
        "notes": a.notes,
    }


@router.get("")
async def list_attendance(course: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(Attendance)
    if course:
        q = q.filter(Attendance.course == course)
    items = q.order_by(Attendance.date.desc()).all()
    return [_serialize(a) for a in items]


@router.post("", response_model=AttendanceResponse)
async def create_attendance(record: AttendanceCreate, db: Session = Depends(get_db)) -> Attendance:
    db_record = Attendance(**record.model_dump())
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record


@router.delete("/{record_id}")
async def delete_attendance(record_id: int, db: Session = Depends(get_db)) -> dict:
    record = db.get(Attendance, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    db.delete(record)
    db.commit()
    return {"ok": True}


@router.get("/stats", response_model=AttendanceStats)
async def attendance_stats(db: Session = Depends(get_db)) -> AttendanceStats:
    total = db.query(Attendance).count()
    present = db.query(func.count()).filter(Attendance.status == "present").scalar()
    absent = db.query(func.count()).filter(Attendance.status == "absent").scalar()
    late = db.query(func.count()).filter(Attendance.status == "late").scalar()
    excused = db.query(func.count()).filter(Attendance.status == "excused").scalar()
    present_weighted = present + late * 0.5
    percentage = round((present_weighted / total) * 100, 1) if total else 100.0
    return AttendanceStats(
        total=total,
        present=present,
        absent=absent,
        late=late,
        excused=excused,
        percentage=percentage,
    )
