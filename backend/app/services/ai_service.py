import httpx
import json
import logging
import re
from datetime import datetime
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


def _time_greeting() -> str:
    hour = datetime.now().hour
    if hour < 5:
        return "Working late!"
    if hour < 12:
        return "Good morning!"
    if hour < 17:
        return "Good afternoon!"
    if hour < 21:
        return "Good evening!"
    return "Good night!"


def _parse_json(text: str):
    if not text:
        raise json.JSONDecodeError("empty response", text, 0)
    cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
    cleaned = re.sub(r"\bTrue\b", "true", cleaned)
    cleaned = re.sub(r"\bFalse\b", "false", cleaned)
    cleaned = re.sub(r"\bNone\b", "null", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = None
        for i, ch in enumerate(cleaned):
            if ch in "[{":
                start = i
                break
        if start is not None:
            open_ch, close_ch = ("[", "]") if cleaned[start] == "[" else ("{", "}")
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except json.JSONDecodeError:
                            break
        raise


def _to_minutes(value: str) -> int:
    try:
        hour, minute = str(value).split(":")
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError):
        return 0


def _minutes_between(start: str, end: str) -> int:
    return max(0, _to_minutes(end) - _to_minutes(start))


class AIService:
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL
        self.model = settings.OLLAMA_MODEL

    async def _call_ollama(
        self, prompt: str, system: Optional[str] = None, max_tokens: int = 300
    ) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system:
            payload["system"] = system

        # One client per call so the same transport is never shared between
        # different event loops (uvicorn loop vs. background sync threads).
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "")
            except httpx.RequestError as e:
                logger.error(f"Ollama request failed: {e}")
                raise
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Ollama response: {e}")
                raise

    async def analyze_email(self, subject: str, body: str) -> dict:
        system = """You are an email analysis assistant for a chief-of-staff AI. Analyze the email and respond ONLY in JSON with this exact shape:
{"category": "reply_required|important|can_ignore|promotion|social", "summary": "one concise sentence capturing the key point", "has_deadline": true/false, "deadline": null or "YYYY-MM-DD", "deadline_description": null or short text, "suggested_reply": null or a short professional reply draft}

Rules:
- reply_required: person wants an answer/action from me
- important: matters to me but no immediate response needed
- promotion: marketing, discounts, promotional offers
- social: social media, LinkedIn notifications
- can_ignore: everything else
- Only set has_deadline/deadline when the email contains a concrete date or deadline. Never invent one."""
        prompt = f"""Email Subject: {subject}

Email Body:
{body[:2500]}

Analyze the email."""
        result = await self._call_ollama(prompt, system, max_tokens=350)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return {
                "category": "unknown",
                "summary": result[:300],
                "has_deadline": False,
                "deadline": None,
                "deadline_description": None,
                "suggested_reply": None,
            }

    async def extract_schedule(self, subject: str, body: str) -> dict:
        system = """You extract scheduling details from an email. Only include items that are explicitly about a meeting, review, exam, deadline, or assignment with a concrete date/time stated in the email. Respond ONLY in JSON:
{"items": [{"kind": "meeting|review|exam|assignment|event", "title": "...", "date": "YYYY-MM-DD or null", "time": "HH:MM 24-hour or null", "end_time": "HH:MM 24-hour or null", "location": null or "...", "link": null or "...", "notes": null or "..."}]}
Rules:
- kind: "meeting" for meetings/webinars/sessions, "review" for project/semester reviews, "exam" for exams/CIE/evaluations/tests, "assignment" for submissions with due dates, "event" for other dated activities.
- Only extract an item if the email contains a concrete date (e.g. "15th August 2026"). Never invent dates. Use year from context (assume 2026 if ambiguous).
- Extract the time EXACTLY as stated and convert to 24-hour "HH:MM". Examples: "12:00 PM" -> "12:00", "08:00 AM" -> "08:00", "12:00 AM" -> "00:00", "4:30 PM" -> "16:30", "23:59 IST" -> "23:59", "from 10:00 AM onwards" -> "10:00". If no time is stated, set time null.
- Extract location (venue/room/building) and link (URL) when present.
- If nothing scheduleable, return {"items": []}.
Only output the JSON object."""
        prompt = f"""Email Subject: {subject}

Email Body:
{body[:3000]}

Extract the schedule items from this email."""
        result = await self._call_ollama(prompt, system, max_tokens=400)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return {"items": []}

    async def classify_email(self, subject: str, body: str) -> dict:
        system = """You are an email classification assistant. Classify the email into one of:
- reply_required: needs a response
- important: important but no immediate reply needed
- can_ignore: not important
- promotion: promotional/marketing
- social: social media notifications

Respond in JSON format: {"category": "...", "reason": "...", "has_deadline": bool, "deadline_description": null or str}"""

        prompt = f"""Email Subject: {subject}

Email Body: {body[:2000]}

Classify this email and determine if it contains any deadlines."""
        result = await self._call_ollama(prompt, system, max_tokens=180)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return {"category": "unknown", "reason": result[:200], "has_deadline": False, "deadline_description": None}

    async def suggest_reply(self, email_body: str, email_subject: str, context: Optional[str] = None) -> str:
        system = "You are an email writing assistant. Draft professional, concise email replies."
        prompt = f"""Original Email Subject: {email_subject}
Original Email Body: {email_body[:2000]}

Context: {context or "No additional context"}

Draft a professional reply:"""
        return await self._call_ollama(prompt, system, max_tokens=320)

    async def summarize_email(self, subject: str, body: str) -> dict:
        system = "You summarize emails concisely. Extract key info including deadlines and action items."
        prompt = f"""Summarize this email and extract any deadlines or action items:

Subject: {subject}
Body: {body[:3000]}

Respond in JSON: {{"summary": "...", "has_deadline": bool, "deadline": null or "YYYY-MM-DD", "deadline_description": null or str, "action_items": [...]}}"""
        result = await self._call_ollama(prompt, system, max_tokens=300)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return {"summary": result[:300], "has_deadline": False, "deadline": None, "deadline_description": None, "action_items": []}

    async def detect_calendar_conflicts(self, events_text: str) -> list[dict]:
        system = "You analyze calendar events and detect scheduling conflicts."
        prompt = f"""Analyze these calendar events for overlaps and conflicts:

{events_text}

For each conflict, describe it and suggest a resolution.
Respond in JSON: [{{"event1": "...", "event2": "...", "description": "...", "suggestion": "..."}}]"""
        result = await self._call_ollama(prompt, system, max_tokens=350)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return []

    async def suggest_free_slots(self, events_text: str, date: str, work_hours: str = "9:00-18:00") -> list[dict]:
        system = "You suggest free time slots in a calendar for scheduling work sessions."
        prompt = f"""Date: {date}
Work Hours: {work_hours}
Existing Events:
{events_text}

Suggest available free slots of at least 1 hour each.
Respond in JSON: [{{"start": "HH:MM", "end": "HH:MM", "duration_minutes": int}}]"""
        result = await self._call_ollama(prompt, system, max_tokens=200)
        try:
            data = _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return []

        if isinstance(data, dict):
            data = data.get("free_slots") or data.get("slots") or data.get("items") or []
        if not isinstance(data, list):
            return []

        slots = []
        for item in data:
            if not isinstance(item, dict):
                continue
            start = str(item.get("start", "")).strip()
            end = str(item.get("end", "")).strip()
            if not start or not end:
                continue
            try:
                duration = int(item.get("duration_minutes") or _minutes_between(start, end))
            except (TypeError, ValueError):
                duration = _minutes_between(start, end)
            if duration <= 0:
                continue
            slots.append({"start": start, "end": end, "duration_minutes": duration})
        return slots

    async def summarize_document(self, text: str) -> dict:
        system = "You summarize documents and extract key points, deadlines, and action items."
        prompt = f"""Summarize this document and extract key information:

{text[:4000]}

Respond in JSON: {{"summary": "...", "key_points": ["...", "..."], "deadlines": null or [{{"description": "...", "date": null or "YYYY-MM-DD"}}], "action_items": ["..."]}}"""
        result = await self._call_ollama(prompt, system, max_tokens=450)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return {"summary": result[:500], "key_points": [], "deadlines": None, "action_items": []}

    async def answer_document_question(self, document_text: str, question: str) -> str:
        system = "You answer questions about documents based on their content."
        prompt = f"""Document Content:
{document_text[:4000]}

Question: {question}

Answer based only on the document content:"""
        return await self._call_ollama(prompt, system, max_tokens=350)

    async def prioritize_tasks(self, tasks_text: str) -> list[dict]:
        system = "You prioritize tasks based on urgency, importance, and deadlines."
        prompt = f"""Prioritize these tasks into critical, high, medium, or low:

{tasks_text}

Respond in JSON: [{{"task_id": int, "priority": "critical|high|medium|low", "reason": "..."}}]"""
        result = await self._call_ollama(prompt, system, max_tokens=280)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return []

    async def generate_daily_briefing(
        self,
        meetings: list,
        important_emails: list,
        pending_tasks: list,
        news_items: list,
    ) -> dict:
        system = "You generate a personalized daily briefing for a busy professional."
        meetings_text = "\n".join([f"- {m.get('title', '')} at {m.get('time', '')} ({m.get('duration', '')})" for m in meetings])
        emails_text = "\n".join([f"- {e.get('subject', '')} from {e.get('from', '')}" for e in important_emails])
        tasks_text = "\n".join([f"- [{t.get('priority', '')}] {t.get('title', '')}" for t in pending_tasks])
        news_text = "\n".join([f"- {n.get('title', '')}: {n.get('summary', '')}" for n in news_items])

        prompt = f"""Generate a morning briefing with a suggested schedule.

Today's Meetings:
{meetings_text or "No meetings today"}

Important Emails:
{emails_text or "No important emails"}

Pending Tasks:
{tasks_text or "No pending tasks"}

Top News:
{news_text or "No news items"}

Respond in JSON:
{{"greeting": "Good Morning!",
"summary": "...",
"meetings_highlights": "...",
"email_highlights": "...",
"task_highlights": "...",
"suggested_plan": "9-11 AM: ...\\n11-12 PM: ...\\n..."}}"""
        result = await self._call_ollama(prompt, system, max_tokens=520)
        try:
            return _parse_json(result)
        except (json.JSONDecodeError, AttributeError):
            return {
                "greeting": _time_greeting(),
                "summary": result[:500],
                "meetings_highlights": "",
                "email_highlights": "",
                "task_highlights": "",
                "suggested_plan": "",
            }

    async def close(self):
        await self.client.aclose()


ai_service = AIService()
