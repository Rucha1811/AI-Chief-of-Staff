from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.core.database import get_db
from app.models.task import Task
from app.schemas.task import TaskResponse, TaskCreate, TaskUpdate, TaskRePrioritize
from app.services.ai_service import ai_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[TaskResponse]:
    query = db.query(Task)
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
async def create_task(task: TaskCreate, db: Session = Depends(get_db)) -> TaskResponse:
    db_task = Task(**task.model_dump())
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@router.get("/stats")
async def task_stats(db: Session = Depends(get_db)) -> dict:
    return {
        "total": db.query(Task).count(),
        "pending": db.query(Task).filter(Task.status == "pending").count(),
        "in_progress": db.query(Task).filter(Task.status == "in_progress").count(),
        "completed": db.query(Task).filter(Task.status == "completed").count(),
        "overdue": db.query(Task).filter(
            Task.status.in_(["pending", "in_progress"]),
            Task.due_date < datetime.now(),
        ).count(),
    }


@router.get("/{task_id}")
async def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskResponse:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}")
async def update_task(
    task_id: int, update: TaskUpdate, db: Session = Depends(get_db)
) -> TaskResponse:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(task, key, val)
    if update.status == "completed":
        task.completed_at = datetime.now()
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}")
async def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.post("/reprioritize")
async def reprioritize_tasks(db: Session = Depends(get_db)) -> list[TaskResponse]:
    tasks = db.query(Task).filter(Task.status.in_(["pending", "in_progress"])).all()
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
            task = db.query(Task).filter(Task.id == tid).first()
            if task:
                task.priority = new_priority
    db.commit()
    return db.query(Task).filter(Task.status.in_(["pending", "in_progress"])).order_by(
        Task.priority.desc(), Task.due_date.asc().nulls_last()
    ).all()
