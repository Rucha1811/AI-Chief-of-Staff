from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.exam import Exam
from app.schemas.exam import ExamCreate, ExamResponse, ExamUpdate

router = APIRouter(prefix="/api/exams", tags=["exams"])


def _serialize(exam: Exam) -> dict:
    return {
        "id": exam.id,
        "title": exam.title,
        "subject": exam.subject,
        "exam_date": exam.exam_date.isoformat() if exam.exam_date else None,
        "duration_minutes": exam.duration_minutes,
        "location": exam.location,
        "notes": exam.notes,
        "status": exam.status,
        "score": exam.score,
        "max_score": exam.max_score,
    }


@router.get("")
async def list_exams(status: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(Exam)
    if status:
        q = q.filter(Exam.status == status)
    exams = q.order_by(Exam.exam_date.asc()).all()
    return [_serialize(e) for e in exams]


@router.post("", response_model=ExamResponse)
async def create_exam(exam: ExamCreate, db: Session = Depends(get_db)) -> Exam:
    db_exam = Exam(**exam.model_dump())
    db.add(db_exam)
    db.commit()
    db.refresh(db_exam)
    return db_exam


@router.put("/{exam_id}", response_model=ExamResponse)
async def update_exam(exam_id: int, update: ExamUpdate, db: Session = Depends(get_db)) -> Exam:
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(exam, key, val)
    db.commit()
    db.refresh(exam)
    return exam


@router.delete("/{exam_id}")
async def delete_exam(exam_id: int, db: Session = Depends(get_db)) -> dict:
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    db.delete(exam)
    db.commit()
    return {"ok": True}
