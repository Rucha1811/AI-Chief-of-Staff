import os
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.core.config import settings
from app.models.document import Document
from app.schemas.document import DocumentResponse, DocumentQuery, DocumentQueryResponse
from app.services.document_processor import process_document
from app.services.ai_service import ai_service

router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("")
async def list_documents(
    limit: int = 50, offset: int = 0, db: Session = Depends(get_db)
) -> list[DocumentResponse]:
    return db.query(Document).order_by(Document.created_at.desc()).offset(offset).limit(limit).all()


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)) -> DocumentResponse:
    allowed_types = {".pdf", ".docx", ".pptx", ".txt", ".png", ".jpg", ".jpeg"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_types:
        raise HTTPException(status_code=400, detail=f"File type {ext} not supported. Supported: {allowed_types}")

    file_path = os.path.join(UPLOAD_DIR, f"{os.urandom(8).hex()}{ext}")
    async with aiofiles.open(file_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    raw_text = process_document(file_path, ext)
    doc = Document(
        filename=file.filename,
        file_type=ext,
        file_size=len(content),
        storage_path=file_path,
        raw_text=raw_text,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    if raw_text:
        try:
            result = await ai_service.summarize_document(raw_text)
            doc.summary = result.get("summary", "")
            key_points = result.get("key_points", [])
            doc.key_points = "\n".join(key_points) if isinstance(key_points, list) else str(key_points)
            deadlines = result.get("deadlines")
            if deadlines:
                doc.deadlines = "\n".join(
                    [f"{d.get('description', '')} - {d.get('date', '')}" for d in deadlines]
                ) if isinstance(deadlines, list) else str(deadlines)
            doc.is_indexed = True
            db.commit()
            db.refresh(doc)
        except Exception as e:
            pass

    return doc


@router.get("/{document_id}")
async def get_document(document_id: int, db: Session = Depends(get_db)) -> DocumentResponse:
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{document_id}")
async def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if os.path.exists(doc.storage_path):
        os.remove(doc.storage_path)
    db.delete(doc)
    db.commit()
    return {"ok": True}


@router.post("/{document_id}/summarize")
async def summarize_document(
    document_id: int, db: Session = Depends(get_db)
) -> DocumentResponse:
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.raw_text:
        raise HTTPException(status_code=400, detail="No text extracted from document")

    result = await ai_service.summarize_document(doc.raw_text)
    doc.summary = result.get("summary", "")
    key_points = result.get("key_points", [])
    doc.key_points = "\n".join(key_points) if isinstance(key_points, list) else str(key_points)
    deadlines = result.get("deadlines")
    if deadlines:
        doc.deadlines = "\n".join(
            [f"{d.get('description', '')} - {d.get('date', '')}" for d in deadlines]
        ) if isinstance(deadlines, list) else str(deadlines)
    doc.is_indexed = True
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/query")
async def query_document(
    query: DocumentQuery, db: Session = Depends(get_db)
) -> DocumentQueryResponse:
    doc = db.query(Document).filter(Document.id == query.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.raw_text:
        raise HTTPException(status_code=400, detail="No text extracted from document")

    answer = await ai_service.answer_document_question(doc.raw_text, query.question)
    return DocumentQueryResponse(
        document_id=query.document_id,
        question=query.question,
        answer=answer,
    )
