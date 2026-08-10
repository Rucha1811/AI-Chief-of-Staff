from datetime import datetime, timedelta

_RULES = {"daily", "weekly", "weekdays", "monthly"}


def _add_month(dt: datetime) -> datetime:
    month = dt.month + 1
    year = dt.year
    if month > 12:
        month = 1
        year += 1
    import calendar

    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _step(rule: str) -> timedelta:
    if rule == "monthly":
        return timedelta(days=28)
    return timedelta(days=1)


def _matches(cursor: datetime, start: datetime, rule: str) -> bool:
    if rule == "daily":
        return True
    if rule == "weekdays":
        return cursor.weekday() < 5
    if rule == "weekly":
        return cursor.weekday() == start.weekday()
    if rule == "monthly":
        return cursor.day == start.day
    return False


def expand_recurrences(event, range_start: datetime, range_end: datetime) -> list[dict]:
    rule = getattr(event, "recurrence_rule", None)
    if not rule or rule not in _RULES:
        return []

    until = getattr(event, "recurrence_until", None)
    start = event.start_time
    duration = event.end_time - start
    occurrences: list[dict] = []
    cursor = start

    while True:
        if rule == "monthly":
            cursor = _add_month(cursor)
        else:
            cursor += timedelta(days=1)
        if cursor > range_end:
            break
        if until and cursor > until:
            break
        if cursor < range_start:
            continue
        if not _matches(cursor, start, rule):
            continue
        occurrences.append(
            {
                "id": event.id,
                "calendar_event_id": None,
                "title": event.title,
                "description": event.description,
                "location": event.location,
                "start_time": cursor,
                "end_time": cursor + duration,
                "is_all_day": False,
                "is_conflict": False,
                "conflict_description": None,
                "suggested_action": None,
                "source": event.source,
                "recurrence_rule": rule,
                "recurrence_until": until,
            }
        )
    return occurrences


def events_in_day(events, day: datetime) -> list:
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    result = []
    for e in events:
        if e.start_time < day_end and e.end_time > day_start:
            result.append(e)
    return result
