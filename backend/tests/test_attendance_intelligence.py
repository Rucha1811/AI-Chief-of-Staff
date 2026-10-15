"""Attendance intelligence: 75% target analysis, recovery maths, priorities.

These are pure-function tests: no database, no network. The point of the suite
is that the dashboard never invents a number. A missing count must surface as
"unknown", a well-defined recovery must be arithmetically exact, and a timetable
that is absent (or does not match) must produce an explanation rather than a
fabricated lecture time.
"""

from datetime import datetime

from app.services import attendance_intelligence as ai

# Thursday 08:00 - an anchor used for "next lecture" ordering.
NOW = datetime(2026, 10, 15, 8, 0)


def _record(name, held, attended, code="", term="Sem 5", percentage=None):
    return {
        "subject_name": name,
        "subject_code": code,
        "term": term,
        "classes_held": held,
        "classes_attended": attended,
        "percentage": percentage,
    }


# --------------------------------------------------------------------------- #
# Percentage + categorisation
# --------------------------------------------------------------------------- #
def test_verified_percentage_only_from_counts():
    assert ai.verified_percentage(40, 48) == 83.3
    assert ai.verified_percentage(32, 50) == 64.0
    assert ai.verified_percentage(0, 0) is None  # undefined, not 0%
    assert ai.verified_percentage(None, 10) is None
    assert ai.verified_percentage(10, None) is None
    assert ai.verified_percentage(0, 10) == 0.0  # a real zero is kept
    assert ai.verified_percentage(5, 4) is None  # inconsistent counts
    assert ai.verified_percentage(-1, 10) is None


def test_categorize_band():
    assert ai.categorize(80, 75) == ai.STATUS_SAFE
    assert ai.categorize(75, 75) == ai.STATUS_SAFE
    assert ai.categorize(66, 75) == ai.STATUS_AT_RISK
    assert ai.categorize(64.9, 75) == ai.STATUS_CRITICAL
    assert ai.categorize(None, 75) == ai.STATUS_UNKNOWN


# --------------------------------------------------------------------------- #
# Recovery maths
# --------------------------------------------------------------------------- #
def test_recovery_classes_needed_is_exact():
    # 70/100 -> 75% needs 20 in a row: (70+20)/(100+20) = 75%.
    assert ai.recovery(70, 100, 75.0) == (20, 0)
    # 74/100 -> 4 in a row: 78/104 = 75%.
    assert ai.recovery(74, 100, 75.0) == (4, 0)
    assert ai.recovery(75, 100, 75.0) == (0, 0)


def test_recovery_skip_headroom_is_exact():
    # 80/100, target 75%: 80/106 = 75.5% ok, 80/107 = 74.8% too low -> skip 6.
    assert ai.recovery(80, 100, 75.0) == (0, 6)
    assert ai.recovery(75, 100, 75.0) == (0, 0)


def test_recovery_is_none_when_history_is_unusable():
    assert ai.recovery(None, None, 75.0) == (None, None)
    assert ai.recovery(None, 10, 75.0) == (None, None)
    assert ai.recovery(5, 0, 75.0) == (None, None)
    assert ai.recovery(5, 4, 75.0) == (None, None)


def test_recovery_degenerate_targets_do_not_divide_by_zero():
    assert ai.recovery(9, 10, 100.0) == (None, 0)  # perfect-only target
    assert ai.recovery(10, 10, 100.0) == (0, 0)
    assert ai.recovery(10, 20, 0.0) == (0, None)


# --------------------------------------------------------------------------- #
# analyze_subject
# --------------------------------------------------------------------------- #
def test_analyze_subject_never_fabricates_a_percentage():
    entry = ai.analyze_subject(_record("DBMS", None, None), 75.0)
    assert entry["percentage"] is None
    assert entry["status"] == ai.STATUS_UNKNOWN
    assert entry["margin"] is None
    assert entry["classes_needed"] is None
    assert entry["classes_can_skip"] is None
    assert entry["data_available"] is False


def test_analyze_subject_falls_back_to_printed_percentage_without_counts():
    entry = ai.analyze_subject(_record("DBMS", None, None, percentage=71.5), 75.0)
    assert entry["percentage"] == 71.5
    assert entry["status"] == ai.STATUS_AT_RISK
    # Recovery is still unknown: no counts means no arithmetic.
    assert entry["classes_needed"] is None
    assert entry["classes_can_skip"] is None


# --------------------------------------------------------------------------- #
# Whole-dashboard analysis
# --------------------------------------------------------------------------- #
def test_empty_records_explains_rather_than_zeros():
    result = ai.analyze([], [], target=75.0, now=NOW)
    assert result["subjects"] == []
    assert result["overall"]["percentage"] is None
    assert result["overall"]["status"] == ai.STATUS_UNKNOWN
    assert result["report"]["headline"] == "No attendance data yet"
    codes = {c["code"] for c in result["caveats"]}
    assert "no_attendance_data" in codes


def test_overall_is_computed_from_counts_and_flagged_below_target():
    records = [
        _record("Data Structures", 48, 40, code="CE301"),
        _record("Operating Systems", 50, 32, code="CE302"),
    ]
    result = ai.analyze(records, [], target=75.0, now=NOW)

    # Only verified counts are summed: 40/48 and 32/50 -> 72/98 = 73.5%
    assert result["overall"]["percentage"] == 73.5
    assert result["overall"]["classes_attended"] == 72
    assert result["overall"]["classes_held"] == 98
    assert result["overall"]["status"] == ai.STATUS_AT_RISK
    assert result["overall"]["below_target_count"] == 1


def test_unknown_subjects_are_counted_but_excluded_from_overall():
    records = [
        _record("Data Structures", 48, 40, code="CE301"),
        _record("DBMS", None, None, code="CE304"),
    ]
    result = ai.analyze(records, [], target=75.0, now=NOW)
    assert result["overall"]["percentage"] == 83.3
    assert result["overall"]["unknown_count"] == 1
    codes = {c["code"] for c in result["caveats"]}
    assert "subjects_without_counts" in codes


def test_priorities_order_critical_before_at_risk():
    records = [
        _record("Safe Subject", 100, 95, code="S1"),
        _record("At Risk", 100, 70, code="S2"),
        _record("Critical", 100, 50, code="S3"),
    ]
    result = ai.analyze(records, [], target=75.0, now=NOW)
    names = [p["subject_name"] for p in result["priorities"]]
    assert names == ["Critical", "At Risk"]


def test_priorities_attach_next_lecture_from_timetable():
    records = [
        _record("Operating Systems", 50, 30, code="CE302"),
        _record("Networks", 40, 20, code="CE303"),
    ]
    timetable = [
        # Monday 09:00 -> 4 days after the Thursday anchor
        {"title": "Operating Systems", "day_of_week": 0, "start_time": "09:00", "end_time": "10:00"},
        # Thursday 09:00 -> today (anchor is 08:00), in 0 days
        {"title": "CE303 Networks", "day_of_week": 3, "start_time": "09:00", "end_time": "10:00"},
    ]
    result = ai.analyze(records, timetable, target=75.0, now=NOW)
    by_name = {p["subject_name"]: p for p in result["priorities"]}

    os_next = by_name["Operating Systems"]["next_lecture"]
    assert os_next["day_name"] == "Monday"
    assert os_next["in_days"] == 4

    net_next = by_name["Networks"]["next_lecture"]
    assert net_next["day_name"] == "Thursday"
    assert net_next["in_days"] == 0
    assert result["timetable_available"] is True


def test_missing_timetable_is_explained():
    records = [_record("Networks", 40, 20, code="CE303")]
    result = ai.analyze(records, [], target=75.0, now=NOW)
    codes = {c["code"] for c in result["caveats"]}
    assert "timetable_missing" in codes
    assert result["priorities"][0]["next_lecture"] is None


def test_unmatched_timetable_is_explained():
    records = [_record("Algorithms", 40, 20, code="CE999")]
    timetable = [
        {"title": "Some Unrelated Elective", "day_of_week": 1, "start_time": "11:00", "end_time": "12:00"}
    ]
    result = ai.analyze(records, timetable, target=75.0, now=NOW)
    codes = {c["code"] for c in result["caveats"]}
    assert "timetable_unmatched" in codes
    assert result["timetable_matched_count"] == 0


def test_report_only_states_numbers_that_exist():
    records = [
        _record("Data Structures", 48, 40, code="CE301"),
        _record("Networks", 40, 20, code="CE303"),
    ]
    result = ai.analyze(records, [], target=75.0, now=NOW)
    report = result["report"]
    assert "75.0" in report["headline"]
    joined = "\n".join(report["lines"])
    assert "Networks" in joined


# --------------------------------------------------------------------------- #
# Timetable matching + next-occurrence ordering
# --------------------------------------------------------------------------- #
def _entry(day, start, title="Operating Systems", end="10:00", location=None):
    return {
        "title": title,
        "day_of_week": day,
        "start_time": start,
        "end_time": end,
        "location": location,
    }


def test_next_occurrence_today_and_after_time_passed():
    today_soon = ai.next_occurrence(_entry(3, "09:00"), NOW)
    assert today_soon["in_days"] == 0
    assert today_soon["day_name"] == "Thursday"

    # The anchor is Thursday 08:00; a 07:00 class already started -> next week.
    passed = ai.next_occurrence(_entry(3, "07:00"), NOW)
    assert passed["in_days"] == 7


def test_next_occurrence_returns_none_for_malformed_entries():
    assert ai.next_occurrence(_entry(3, "not-a-time"), NOW) is None
    assert ai.next_occurrence({"day_of_week": None, "start_time": "09:00"}, NOW) is None


def test_matches_subject_is_conservative():
    # Exact code inside the title.
    assert ai.matches_subject("CS-201 Data Structures", "", "CS201")
    # Normalised substring of the subject name.
    assert ai.matches_subject("Operating Systems Lecture", "Operating Systems", "")
    assert ai.matches_subject("Data Structures", "data structures", "")
    # Two significant shared words.
    assert ai.matches_subject("Advanced Computer Networks Lab", "Computer Networks", "XX")
    # A single shared word is not enough (avoids the wrong lecture).
    assert not ai.matches_subject("Computer Networks", "Computer Graphics", "")
