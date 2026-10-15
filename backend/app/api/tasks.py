from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.core.database import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskResponse, TaskCreate, TaskUpdate, TaskRePrioritize
from app.services.ai_service import ai_service
from app.api.deps import get_current_user
from app.core.limits import clamp_limit, clamp_offset

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_ACTIVE_STATUSES = ["pending", "in_progress"]


def _get_owned(db: Session, user_id: int, task_id: int) -> Task:
    task = db.query(Task).filter(Task.user_id == user_id, Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskResponse]:
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    query = db.query(Task).filter(Task.user_id == user.id)
    if status:
        query = query.filter(Task.status == status)
    if priority:
        query = query.filter(Task.priority == priority)
    if source:
        query = query.filter(Task.source == source)
    return query.order_by(
        Task.priority.desc(),
        Task.due_date.asc().nulls_last(),
    ).offset(offset).limit(limit).all()


@router.post("")
async def create_task(
    task: TaskCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskResponse:
    db_task = Task(**task.model_dump(), user_id=user.id)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@router.get("/stats")
async def task_stats(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    base = db.query(Task).filter(Task.user_id == user.id)
    return {
        "total": base.count(),
        "pending": db.query(Task).filter(Task.user_id == user.id, Task.status == "pending").count(),
        "in_progress": db.query(Task).filter(Task.user_id == user.id, Task.status == "in_progress").count(),
        "completed": db.query(Task).filter(Task.user_id == user.id, Task.status == "completed").count(),
        "overdue": db.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(_ACTIVE_STATUSES),
            Task.due_date < datetime.now(),
        ).count(),
    }


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskResponse:
    return _get_owned(db, user.id, task_id)


@router.put("/{task_id}")
async def update_task(
    task_id: int,
    update: TaskUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskResponse:
    task = _get_owned(db, user.id, task_id)
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(task, key, val)
    if update.status == "completed":
        task.completed_at = datetime.now()
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = _get_owned(db, user.id, task_id)
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.post("/reprioritize")
async def reprioritize_tasks(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TaskResponse]:
    tasks = (
        db.query(Task)
        .filter(Task.user_id == user.id, Task.status.in_(_ACTIVE_STATUSES))
        .all()
    )
    if not tasks:
        return []

    tasks_text = "\n".join([
        f"ID: {t.id} | Title: {t.title} | Due: {t.due_date.strftime('%Y-%m-%d') if t.due_date else 'None'} | Current Priority: {t.priority}"
        for t in tasks
    ])
    priorities = await ai_service.prioritize_tasks(tasks_text)

    for p in priorities:
        tid = p.get("task_id")
        new_priority = p.get("priority")
        if tid and new_priority:
            task = (
                db.query(Task)
                .filter(Task.user_id == user.id, Task.id == tid)
                .first()
            )
            if task:
                task.priority = new_priority
    db.commit()
    return (
        db.query(Task)
        .filter(Task.user_id == user.id, Task.status.in_(_ACTIVE_STATUSES))
        .order_by(Task.priority.desc(), Task.due_date.asc().nulls_last())
        .all()
    )