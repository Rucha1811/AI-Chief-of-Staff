from fastapi import APIRouter, HTTPException
from typing import Optional

from app.services.news_service import fetch_news

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("")
async def get_news(
    topic: Optional[str] = "technology", limit: int = 20
) -> dict:
    try:
        items = await fetch_news(topic=topic or "technology", limit=limit)
        return {"topic": topic or "technology", "items": items, "cached": False}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"News temporarily unavailable: {e}")
