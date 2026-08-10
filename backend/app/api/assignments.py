from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.models.assignment import Assignment
from app.schemas.assignment import AssignmentCreate, AssignmentResponse, AssignmentUpdate

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


def _serialize(a: Assignment) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "course": a.course,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "status": a.status,
        "points_earned": a.points_earned,
        "points_max": a.points_max,
        "priority": a.priority,
        "source": a.source,
        "source_id": a.source_id,
        "external_url": a.external_url,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("")
async def list_assignments(
    status: str | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    q = db.query(Assignment)
    if status:
        q = q.filter(Assignment.status == status)
    items = q.order_by(Assignment.due_date.asc().nulls_last()).all()
    return [_serialize(a) for a in items]


@router.post("", response_model=AssignmentResponse)
async def create_assignment(assignment: AssignmentCreate, db: Session = Depends(get_db)) -> Assignment:
    db_assignment = Assignment(**assignment.model_dump())
    db.add(db_assignment)
    db.commit()
    db.refresh(db_assignment)
    return db_assignment


@router.put("/{assignment_id}", response_model=AssignmentResponse)
async def update_assignment(
    assignment_id: int, update: AssignmentUpdate, db: Session = Depends(get_db)
) -> Assignment:
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(assignment, key, val)
    db.commit()
    db.refresh(assignment)
    return assignment


@router.delete("/{assignment_id}")
async def delete_assignment(assignment_id: int, db: Session = Depends(get_db)) -> dict:
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    db.delete(assignment)
    db.commit()
    return {"ok": True}


@router.post("/sync-classroom")
async def sync_classroom_assignments() -> dict:
    from datetime import datetime

    from app.services.classroom_service import list_assignments

    try:
        assignments = await list_assignments()
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    created = 0
    skipped = 0
    with SessionLocal() as db:
        existing = {
            a.source_id
            for a in db.query(Assignment).filter(Assignment.source == "classroom").all()
        }
        for a in assignments:
            if a["id"] in existing:
                skipped += 1
                continue
            db.add(
                Assignment(
                    title=a["title"],
                    course=a.get("course_name"),
                    due_date=datetime.fromisoformat(a["due"]) if a.get("due") else None,
                    status="todo",
                    points_max=a.get("max_points"),
                    priority="high",
                    source="classroom",
                    source_id=a["id"],
                    external_url=f"https://classroom.google.com/w/c/{a['course_id']}/t/{a['id']}",
                )
            )
            created += 1
        db.commit()
    return {"fetched": len(assignments), "created": created, "skipped": skipped}
