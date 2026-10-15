"""Attendance intelligence: 75% target analysis, recovery maths and priorities.

Pure functions over attendance rows the connector already retrieved. Nothing
here invents a number:

- a percentage is derived only from verified ``attended``/``held`` counts (or
  taken from what the portal itself printed when counts are absent);
- a subject whose counts are missing is reported as ``unknown``, not as 0%;
- recovery figures ("classes needed", "classes you can still miss") are produced
  only when the maths is well-defined, otherwise they are ``None``.

The output feeds the student's attendance dashboard: subject-wise percentages,
how far each subject is from the target, how many upcoming classes must be
attended to recover, which lectures to prioritise, and a short report that
explains what data is missing.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Optional

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

STATUS_SAFE = "safe"
STATUS_AT_RISK = "at_risk"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"

# A subject within this many percentage points below the target is "at risk";
# further below it is "critical". Purely a presentation threshold.
AT_RISK_BAND = 10.0
_STATUS_RANK = {STATUS_CRITICAL: 0, STATUS_AT_RISK: 1, STATUS_UNKNOWN: 2, STATUS_SAFE: 3}

# Words that carry no subject identity and would cause false timetable matches.
_STOPWORDS = {
    "and", "of", "the", "for", "with", "lab", "laboratory",
    "lecture", "lectures", "tutorial", "tutorials", "practical",
    "class", "classes", "course", "session", "period",
}


def verified_percentage(
    attended: Optional[int], held: Optional[int]
) -> Optional[float]:
    """Percentage from verified counts only; ``None`` when it is not defined."""
    if attended is None or held is None:
        return None
    if held <= 0 or attended < 0 or attended > held:
        return None
    return round(attended / held * 100.0, 1)


def categorize(percentage: Optional[float], target: float) -> str:
    """Classify a percentage against the target (``unknown`` when None)."""
    if percentage is None:
        return STATUS_UNKNOWN
    if percentage >= target:
        return STATUS_SAFE
    if percentage >= target - AT_RISK_BAND:
        return STATUS_AT_RISK
    return STATUS_CRITICAL


def recovery(
    attended: Optional[int], held: Optional[int], target_pct: float
) -> tuple[Optional[int], Optional[int]]:
    """Return ``(classes_needed, classes_can_skip)`` for the target.

    ``classes_needed`` is the minimum number of *consecutive* future classes to
    attend so the subject climbs back to the target (0 when it is already at or
    above it). ``classes_can_skip`` is how many upcoming classes may be missed
    while staying at or above the target.

    Both are ``None`` when there is no usable history (missing counts, a
    non-positive denominator, or inconsistent data), and the degenerate targets
    are handled so the maths never divides by zero.
    """
    if attended is None or held is None:
        return None, None
    if held <= 0 or attended < 0 or attended > held:
        return None, None

    target = target_pct / 100.0
    current = attended / held
    if current < target:
        if target >= 1.0:
            # Only reachable by already being perfect; no finite class count.
            return None, 0
        needed = math.ceil((target * held - attended) / (1.0 - target) - 1e-9)
        return max(0, needed), 0
    if target <= 0.0:
        return 0, None
    skippable = math.floor(attended / target - held + 1e-9)
    return 0, max(0, skippable)


def analyze_subject(record: dict, target: float) -> dict:
    """Analyse one subject row into a dashboard entry."""
    held = record.get("classes_held")
    attended = record.get("classes_attended")
    data_available = held is not None and attended is not None

    percentage = verified_percentage(attended, held)
    if percentage is None:
        # Fall back to what the portal printed, if anything. Never invented.
        printed = record.get("percentage")
        percentage = printed if isinstance(printed, (int, float)) else None

    needed, skippable = recovery(attended, held, target)

    return {
        "subject_name": record.get("subject_name") or "",
        "subject_code": record.get("subject_code") or "",
        "term": record.get("term") or "",
        "classes_held": held,
        "classes_attended": attended,
        "percentage": percentage,
        "target": target,
        "status": categorize(percentage, target),
        "margin": round(percentage - target, 1) if percentage is not None else None,
        "classes_needed": needed,
        "classes_can_skip": skippable,
        "data_available": data_available,
    }


# ---------------------------------------------------------------------------
# Timetable matching + next-lecture ordering
# ---------------------------------------------------------------------------


def _identity_tokens(text: Optional[str]) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def matches_subject(
    entry_title: Optional[str], subject_name: str, subject_code: str
) -> bool:
    """Conservative timetable-title -> subject match.

    Requires a subject-code hit, a normalised substring hit, or at least two
    shared significant words. Erring toward "no match" avoids attributing a
    lecture to the wrong subject.
    """
    title = (entry_title or "").lower()
    code = (subject_code or "").lower().strip()
    if code and code in title:
        return True
    norm_title = re.sub(r"[^a-z0-9]+", "", title)
    norm_name = re.sub(r"[^a-z0-9]+", "", (subject_name or "").lower())
    norm_code = re.sub(r"[^a-z0-9]+", "", code)
    # Compare normalised forms too, so "CS-201" matches the code "CS201".
    if norm_code and norm_code in norm_title:
        return True
    if norm_name and (norm_name in norm_title or norm_title in norm_name):
        return True
    return len(_identity_tokens(title) & _identity_tokens(subject_name)) >= 2


def next_occurrence(entry: dict, now: datetime) -> Optional[dict]:
    """When the timetable entry next occurs relative to ``now``.

    ``day_of_week`` is 0=Monday, matching the timetable model.
    """
    day = entry.get("day_of_week")
    start = entry.get("start_time") or ""
    if day is None or not (0 <= int(day) <= 6) or ":" not in start:
        return None
    try:
        hour, minute = start.split(":")[:2]
        start_hhmm = (int(hour), int(minute))
    except (ValueError, TypeError):
        return None

    in_days = (int(day) - now.weekday()) % 7
    if int(day) == now.weekday() and (now.hour, now.minute) >= start_hhmm:
        in_days = 7
    return {
        "day_name": DAYS[int(day)],
        "start_time": start,
        "end_time": entry.get("end_time") or "",
        "location": entry.get("location"),
        "in_days": in_days,
        "title": entry.get("title") or "",
    }


def _next_lecture_for(
    subject: dict, timetable: list[dict], now: datetime
) -> Optional[dict]:
    best: Optional[dict] = None
    for entry in timetable:
        if not matches_subject(
            entry.get("title"), subject["subject_name"], subject["subject_code"]
        ):
            continue
        occurrence = next_occurrence(entry, now)
        if occurrence is None:
            continue
        if best is None or (occurrence["in_days"], occurrence["start_time"]) < (
            best["in_days"],
            best["start_time"],
        ):
            best = occurrence
    return best


# ---------------------------------------------------------------------------
# Whole-dashboard analysis
# ---------------------------------------------------------------------------


def analyze(
    records: list[dict],
    timetable: Optional[list[dict]] = None,
    *,
    target: float,
    now: datetime,
) -> dict:
    """Build the student's attendance dashboard from retrieved rows.

    ``records`` are serialized connector rows; ``timetable`` is the user's
    timetable (title/day_of_week/start_time/...). Any of it may be empty, and
    the result says what is missing rather than filling the gaps.
    """
    timetable = timetable or []
    subjects = [analyze_subject(record, target) for record in records]

    # Overall figures use only rows with a verifiable numerator AND denominator.
    held_total = sum(
        s["classes_held"]
        for s in subjects
        if s["classes_held"] is not None and s["classes_attended"] is not None
    )
    attended_total = sum(
        s["classes_attended"]
        for s in subjects
        if s["classes_held"] is not None and s["classes_attended"] is not None
    )
    overall_percentage = verified_percentage(attended_total, held_total)
    overall_needed, overall_skippable = recovery(attended_total, held_total, target)

    below_target = [s for s in subjects if s["status"] in (STATUS_AT_RISK, STATUS_CRITICAL)]
    unknown = [s for s in subjects if s["status"] == STATUS_UNKNOWN]

    # Priorities: the subjects that need attention, most urgent first. A subject
    # is only ordered by a lecture time when the timetable actually matches it.
    priorities = []
    for subject in sorted(
        below_target,
        key=lambda s: (
            _STATUS_RANK[s["status"]],
            s["margin"] if s["margin"] is not None else 0.0,
            s["subject_name"],
        ),
    ):
        lecture = _next_lecture_for(subject, timetable, now)
        priorities.append({**subject, "next_lecture": lecture})

    matched = sum(1 for p in priorities if p["next_lecture"] is not None)

    caveats: list[dict] = []
    if not records:
        caveats.append(
            {
                "code": "no_attendance_data",
                "message": (
                    "No attendance has been retrieved from e-Governance yet. "
                    "Connect or import your attendance page to see this dashboard."
                ),
            }
        )
    if unknown:
        caveats.append(
            {
                "code": "subjects_without_counts",
                "message": (
                    f"{len(unknown)} subject(s) have no published class counts, so "
                    "their percentage is unknown rather than zero."
                ),
            }
        )
    if not timetable:
        caveats.append(
            {
                "code": "timetable_missing",
                "message": (
                    "No timetable is set up, so upcoming lectures cannot be placed "
                    "on a day/time. Add your timetable to prioritise lectures."
                ),
            }
        )
    elif priorities and matched == 0:
        caveats.append(
            {
                "code": "timetable_unmatched",
                "message": (
                    "Your timetable entries could not be matched to these subjects, "
                    "so next-lecture times are unavailable."
                ),
            }
        )

    report = _build_report(
        subjects, overall_percentage, target, priorities, matched, timetable
    )

    return {
        "target": target,
        "subjects": subjects,
        "overall": {
            "percentage": overall_percentage,
            "classes_held": held_total,
            "classes_attended": attended_total,
            "target": target,
            "status": categorize(overall_percentage, target),
            "classes_needed": overall_needed,
            "classes_can_skip": overall_skippable,
            "subject_count": len(subjects),
            "below_target_count": len(below_target),
            "unknown_count": len(unknown),
        },
        "priorities": priorities,
        "timetable_available": bool(timetable),
        "timetable_matched_count": matched,
        "caveats": caveats,
        "report": report,
    }


def _build_report(
    subjects: list[dict],
    overall_percentage: Optional[float],
    target: float,
    priorities: list[dict],
    matched: int,
    timetable: list[dict],
) -> dict:
    """A short, data-derived summary. Mentions only numbers that exist."""
    if not subjects:
        return {
            "headline": "No attendance data yet",
            "lines": [
                "Nothing has been synchronized from e-Governance, so there is no "
                "analysis to show. Attendance is never estimated."
            ],
        }

    lines: list[str] = []
    if overall_percentage is None:
        headline = "Attendance overview unavailable"
        lines.append(
            "No overall percentage can be computed because no subject reported "
            "both classes held and classes attended."
        )
    else:
        verdict = "on or above" if overall_percentage >= target else "below"
        headline = f"Overall attendance {overall_percentage}% ({verdict} the {target}% target)"
        lines.append(
            f"Across {len(subjects)} subject(s), overall attendance is "
            f"{overall_percentage}% against a {target}% target."
        )

    from_status = {STATUS_CRITICAL: [], STATUS_AT_RISK: [], STATUS_UNKNOWN: []}
    for subject in subjects:
        from_status.setdefault(subject["status"], []).append(subject)

    critical = from_status.get(STATUS_CRITICAL, [])
    at_risk = from_status.get(STATUS_AT_RISK, [])
    unknown = from_status.get(STATUS_UNKNOWN, [])
    safe = from_status.get(STATUS_SAFE, [])

    if critical:
        names = ", ".join(
            f"{s['subject_name']} ({s['percentage']}%)" for s in critical[:5]
        )
        lines.append(f"{len(critical)} subject(s) are well below target: {names}.")
    if at_risk:
        names = ", ".join(
            f"{s['subject_name']} ({s['percentage']}%)" for s in at_risk[:5]
        )
        lines.append(f"{len(at_risk)} subject(s) are close to the line: {names}.")
    if safe and not critical and not at_risk:
        lines.append("Every subject with data is at or above the target.")
    if unknown:
        lines.append(
            f"{len(unknown)} subject(s) have no counts and are excluded from the "
            "overall figure."
        )

    for subject in priorities[:3]:
        if subject["classes_needed"]:
            lines.append(
                f"Attend the next {subject['classes_needed']} {subject['subject_name']} "
                f"class(es) without a miss to reach {target}%."
            )
        if subject.get("next_lecture"):
            lecture = subject["next_lecture"]
            when = "today" if lecture["in_days"] == 0 else f"in {lecture['in_days']} day(s)"
            lines.append(
                f"Next {subject['subject_name']} lecture: {lecture['day_name']} "
                f"{lecture['start_time']} ({when})."
            )

    skippable = [s for s in subjects if s["classes_can_skip"]]
    if skippable:
        best = max(skippable, key=lambda s: s["classes_can_skip"])
        lines.append(
            f"Most headroom: {best['subject_name']} — you can still miss "
            f"{best['classes_can_skip']} class(es) and stay at/above {target}%."
        )

    if timetable and priorities and matched == 0:
        lines.append(
            "Lecture times are unavailable because timetable entries did not "
            "match these subjects."
        )

    return {"headline": headline, "lines": lines}
