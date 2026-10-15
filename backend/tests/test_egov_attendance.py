"""CHARUSAT e-Governance attendance connector tests.

Covers the read-only connector end to end: HTML parsing and normalisation,
safe percentage maths, missing/invalid/duplicate data handling, the bounded
retry + timeout behaviour of the fetcher, expired-session detection, the
last-known-good / stale snapshot behaviour, per-user isolation and independence
from the Google integration.

Nothing here touches the real university portal: the fetcher is driven either
through ``httpx.MockTransport`` or by monkeypatching the module-level
``fetch_attendance_html``.
"""

import httpx
import pytest
from datetime import timedelta

from app.models.user_integration import UserIntegration
from app.services import egov_service
from app.services.egov_service import (
    EGovError,
    EGovParseError,
    EGovSessionExpired,
    EGovUnavailable,
    compute_percentage,
    fetch_attendance_html,
    parse_attendance_html,
)

from tests.conftest import auth

PORTAL_URL = "https://support.charusat.edu.in/Egov/StudentAttendance.aspx"


def _grant_charusat_identity(db, user_id: int, email: str) -> None:
    """Mark a test user as a provider-verified CHARUSAT identity.

    The connector is student-only, so the account fixtures used below must be
    eligible. Production never sets these fields from user input - only from a
    provider's verified claim - but a test needs a shortcut for that.
    """
    from app.models.user import User

    user = db.get(User, user_id)
    user.email_verified = True
    user.verified_email = email
    db.commit()


@pytest.fixture(autouse=True)
def _eligible_students(request, db):
    """Make the standard ``owner``/``member`` fixtures eligible when a test uses
    them, without touching tests that do not (the pure parser/fetcher tests)."""
    for name in ("owner", "member"):
        if name in request.fixturenames:
            fixture = request.getfixturevalue(name)
            _grant_charusat_identity(db, fixture["id"], f"{name}@charusat.edu.in")



def attendance_page(rows: str = "", *, with_sr: bool = True) -> str:
    sr = "<th>Sr No</th>" if with_sr else ""
    return f"""
    <html><body><form id="form1">
      <table id="gvAttendance" class="table table-bordered">
        <tr>{sr}<th>Subject Code</th><th>Subject</th><th>Lectures Held</th>
            <th>Lectures Attended</th><th>Attendance %</th></tr>
        {rows}
      </table>
    </form></body></html>
    """


ROW_DS = "<tr><td>1</td><td>CE301</td><td>Data Structures</td><td>48</td><td>40</td><td>83.3</td></tr>"
ROW_OS = "<tr><td>2</td><td>CE302</td><td>Operating Systems</td><td>50</td><td>25</td><td>50</td></tr>"


# ---------------------------------------------------------------------------
# Parsing / normalisation
# ---------------------------------------------------------------------------


def test_parse_basic_attendance_table():
    result = parse_attendance_html(attendance_page(ROW_DS + ROW_OS))
    by_code = {r.subject_code: r for r in result.rows}
    assert set(by_code) == {"CE301", "CE302"}

    ds = by_code["CE301"]
    assert ds.subject_name == "Data Structures"
    assert ds.classes_held == 48
    assert ds.classes_attended == 40
    assert ds.percentage == 83.3  # derived from counts, not the printed 83.3

    os_row = by_code["CE302"]
    assert (os_row.classes_held, os_row.classes_attended) == (50, 25)
    assert os_row.percentage == 50.0


def test_parse_derives_percentage_from_counts_over_printed_value():
    # Printed value is wrong/misleading; the verified counts must win.
    page = attendance_page(
        "<tr><td>1</td><td>X</td><td>Maths</td><td>10</td><td>7</td><td>1</td></tr>"
    )
    (row,) = parse_attendance_html(page).rows
    assert row.percentage == 70.0


def test_parse_combined_attended_over_total_cell():
    page = """
    <table>
      <tr><th>Course</th><th>Lectures</th></tr>
      <tr><td>Physics</td><td>28/40</td></tr>
    </table>
    """
    (row,) = parse_attendance_html(page).rows
    assert row.classes_held == 40
    assert row.classes_attended == 28
    assert row.percentage == 70.0


def test_parse_zero_denominator_is_not_fabricated():
    page = attendance_page(
        "<tr><td>1</td><td>Z</td><td>Zero Class</td><td>0</td><td>0</td><td></td></tr>"
    )
    (row,) = parse_attendance_html(page).rows
    assert row.classes_held == 0
    assert row.classes_attended == 0
    assert row.percentage is None


def test_parse_missing_values_stay_none_not_zero():
    page = attendance_page(
        "<tr><td>1</td><td>M</td><td>Missing</td><td></td><td></td><td></td></tr>"
    )
    (row,) = parse_attendance_html(page).rows
    assert row.classes_held is None
    assert row.classes_attended is None
    assert row.percentage is None


def test_parse_inconsistent_attended_greater_than_held_gives_no_percentage():
    page = attendance_page(
        "<tr><td>1</td><td>I</td><td>Bad Data</td><td>10</td><td>15</td><td></td></tr>"
    )
    (row,) = parse_attendance_html(page).rows
    assert row.classes_held == 10
    assert row.classes_attended == 15
    assert row.percentage is None


def test_parse_duplicate_rows_keep_last_and_skip_total():
    page = attendance_page(
        ROW_DS
        + "<tr><td>2</td><td>CE301</td><td>Data Structures</td><td>48</td><td>44</td><td></td></tr>"
        + "<tr><td></td><td></td><td>Total</td><td>48</td><td>44</td><td>91.6</td></tr>"
    )
    result = parse_attendance_html(page)
    assert len(result.rows) == 1
    assert result.rows[0].classes_attended == 44  # last occurrence wins
    assert result.skipped >= 1  # the Total row was dropped


def test_parse_empty_table_is_not_an_error():
    result = parse_attendance_html(attendance_page(""))
    assert result.rows == []
    assert result.skipped == 0


def test_parse_login_page_detected_as_expired_session():
    with pytest.raises(EGovSessionExpired):
        parse_attendance_html(
            '<html><input id="txtPassword"/><img src="Captcha.ashx"/></html>'
        )


def test_parse_page_without_attendance_table_raises():
    with pytest.raises(EGovParseError):
        parse_attendance_html("<html><body><p>No table here</p></body></html>")


def test_parse_empty_input_raises():
    with pytest.raises(EGovParseError):
        parse_attendance_html("   ")


def test_parse_headerless_body_table_uses_td_headers():
    page = """
    <table>
      <tr><td>Subject</td><td>Classes Held</td><td>Classes Attended</td></tr>
      <tr><td>Networks</td><td>30</td><td>24</td></tr>
    </table>
    """
    (row,) = parse_attendance_html(page).rows
    assert row.subject_name == "Networks"
    assert row.percentage == 80.0


@pytest.mark.parametrize(
    "attended,held,expected",
    [
        (40, 48, 83.3),
        (25, 50, 50.0),
        (1, 0, None),  # zero denominator
        (5, 4, None),  # attended > held is inconsistent
        (-1, 10, None),  # negative numerator
        (None, 10, None),  # missing numerator
        (3, None, None),  # missing denominator
        (0, 10, 0.0),
    ],
)
def test_compute_percentage_is_conservative(attended, held, expected):
    assert compute_percentage(attended, held) == expected


# ---------------------------------------------------------------------------
# Fetcher: bounded retries, timeouts, redirects, host pinning
# ---------------------------------------------------------------------------


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetcher_returns_body_on_success():
    client = _client(lambda request: httpx.Response(200, text=attendance_page(ROW_DS)))
    html = fetch_attendance_html(PORTAL_URL, "cookie", client=client, base_delay=0.0)
    assert "gvAttendance" in html


def test_fetcher_retries_5xx_are_bounded():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="down")

    with pytest.raises(EGovUnavailable):
        fetch_attendance_html(
            PORTAL_URL, "cookie", client=_client(handler), max_retries=2, base_delay=0.0
        )
    assert calls["n"] == 3  # initial attempt + 2 retries, then stop


def test_fetcher_timeout_is_retried_then_bounded():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(EGovUnavailable):
        fetch_attendance_html(
            PORTAL_URL, "cookie", client=_client(handler), max_retries=1, base_delay=0.0
        )
    assert calls["n"] == 2


def test_fetcher_does_not_retry_rejected_session():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    with pytest.raises(EGovSessionExpired):
        fetch_attendance_html(
            PORTAL_URL, "cookie", client=_client(handler), max_retries=3, base_delay=0.0
        )
    assert calls["n"] == 1  # a rejected session is never retried


def test_fetcher_detects_login_redirect():
    def handler(request):
        return httpx.Response(
            302, headers={"location": "https://support.charusat.edu.in/Egov/home.aspx"}
        )

    with pytest.raises(EGovSessionExpired):
        fetch_attendance_html(PORTAL_URL, "cookie", client=_client(handler), base_delay=0.0)


def test_fetcher_detects_login_page_body():
    def handler(request):
        return httpx.Response(200, text='<input id="txtPassword" name="txtPassword"/>')

    with pytest.raises(EGovSessionExpired):
        fetch_attendance_html(PORTAL_URL, "cookie", client=_client(handler), base_delay=0.0)


def test_fetcher_refuses_cross_host_redirect():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    with pytest.raises(EGovError):
        fetch_attendance_html(PORTAL_URL, "cookie", client=_client(handler), base_delay=0.0)


def test_validate_attendance_url_rejects_off_host_and_bad_scheme():
    with pytest.raises(EGovError):
        egov_service.validate_attendance_url("http://evil.example/attendance")
    with pytest.raises(EGovError):
        egov_service.validate_attendance_url("ftp://support.charusat.edu.in/x")
    assert (
        egov_service.validate_attendance_url(PORTAL_URL) == PORTAL_URL
    )


# ---------------------------------------------------------------------------
# API: connect / import / refresh / disconnect / isolation
# ---------------------------------------------------------------------------


def test_connection_is_private_requires_auth(client):
    assert client.get("/api/attendance/egov").status_code == 401


def test_import_stores_snapshot(client, owner):
    headers = auth(owner["token"])
    r = client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_DS + ROW_OS)},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "connected"
    assert body["record_count"] == 2
    assert body["imported"] == 2
    assert body["overall_percentage"] == 66.3  # 65/98
    assert body["last_synced_at"] is not None

    listed = client.get("/api/attendance/egov", headers=headers).json()
    assert listed["record_count"] == 2
    assert {rec["subject_code"] for rec in listed["records"]} == {"CE301", "CE302"}


def test_import_rejects_page_without_table(client, owner):
    r = client.post(
        "/api/attendance/egov/import",
        json={"html": "<html><body>nothing</body></html>"},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 422


def test_import_rejects_login_page(client, owner):
    r = client.post(
        "/api/attendance/egov/import",
        json={"html": '<input id="txtPassword"/><img src="Captcha.ashx">'},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 409


def test_session_connect_fetches_and_never_echoes_cookie(client, owner, db, monkeypatch):
    cookie = "ASP.NET_SessionId=super-secret-token"
    monkeypatch.setattr(
        egov_service,
        "fetch_attendance_html",
        lambda url, ck, **kw: attendance_page(ROW_DS),
    )
    r = client.post(
        "/api/attendance/egov/session",
        json={"session_cookie": cookie, "attendance_url": PORTAL_URL},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 200, r.text
    assert cookie not in r.text
    assert "super-secret-token" not in r.text

    body = r.json()
    assert body["state"] == "connected"
    assert body["method"] == "session"
    assert body["record_count"] == 1

    # Stored encrypted, not in clear.
    row = egov_service.get_connection(db, owner["id"])
    assert row is not None
    assert cookie not in (row.refresh_token_encrypted or "")


def test_session_connect_rejects_off_host_url(client, owner):
    r = client.post(
        "/api/attendance/egov/session",
        json={"session_cookie": "x", "attendance_url": "http://evil.example/att"},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 400


def test_refresh_expired_session_keeps_last_known_good(client, owner, monkeypatch):
    # First a good import so there is a snapshot.
    client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_DS)},
        headers=auth(owner["token"]),
    )
    monkeypatch.setattr(
        egov_service,
        "fetch_attendance_html",
        lambda url, ck, **kw: attendance_page(ROW_DS),
    )
    client.post(
        "/api/attendance/egov/session",
        json={"session_cookie": "ck", "attendance_url": PORTAL_URL},
        headers=auth(owner["token"]),
    )

    # Now the portal rejects the session.
    def expired(url, ck, **kw):
        raise EGovSessionExpired("session expired")

    monkeypatch.setattr(egov_service, "fetch_attendance_html", expired)
    r = client.post("/api/attendance/egov/refresh", headers=auth(owner["token"]))
    assert r.status_code == 409

    after = client.get("/api/attendance/egov", headers=auth(owner["token"])).json()
    assert after["state"] == "reauth_required"
    assert after["record_count"] == 1  # snapshot preserved, not blanked


def test_refresh_unavailable_marks_connection(client, owner, monkeypatch):
    monkeypatch.setattr(
        egov_service, "fetch_attendance_html", lambda url, ck, **kw: attendance_page(ROW_DS)
    )
    client.post(
        "/api/attendance/egov/session",
        json={"session_cookie": "ck", "attendance_url": PORTAL_URL},
        headers=auth(owner["token"]),
    )

    def down(url, ck, **kw):
        raise EGovUnavailable("portal down")

    monkeypatch.setattr(egov_service, "fetch_attendance_html", down)
    r = client.post("/api/attendance/egov/refresh", headers=auth(owner["token"]))
    assert r.status_code == 503

    after = client.get("/api/attendance/egov", headers=auth(owner["token"])).json()
    assert after["state"] == "unavailable"
    assert after["record_count"] == 1


def test_stale_snapshot_is_labelled(client, owner, db):
    client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_DS)},
        headers=auth(owner["token"]),
    )
    row = egov_service.get_connection(db, owner["id"])
    row.last_synced_at = egov_service.utcnow() - timedelta(hours=48)
    db.commit()

    body = client.get("/api/attendance/egov", headers=auth(owner["token"])).json()
    assert body["state"] == "stale"
    assert body["stale"] is True
    assert body["record_count"] == 1


def test_attendance_is_isolated_between_users(client, owner, member):
    client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_DS + ROW_OS)},
        headers=auth(owner["token"]),
    )

    # The other user sees nothing of the owner's data.
    other = client.get("/api/attendance/egov", headers=auth(member["token"])).json()
    assert other["state"] == "not_connected"
    assert other["record_count"] == 0

    # ...and cannot disconnect the owner's connection.
    client.delete("/api/attendance/egov", headers=auth(member["token"]))
    still_there = client.get("/api/attendance/egov", headers=auth(owner["token"])).json()
    assert still_there["record_count"] == 2


def test_disconnect_removes_snapshot_but_not_google(client, owner, db):
    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            account_id="student@charusat.edu.in",
            refresh_token_encrypted="encrypted-google-token",
        )
    )
    db.commit()

    client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_DS)},
        headers=auth(owner["token"]),
    )
    r = client.delete("/api/attendance/egov", headers=auth(owner["token"]))
    assert r.status_code == 200 and r.json()["disconnected"] is True

    after = client.get("/api/attendance/egov", headers=auth(owner["token"])).json()
    assert after["state"] == "not_connected"
    assert after["record_count"] == 0

    # The Google connection survives untouched.
    db.expire_all()
    google = (
        db.query(UserIntegration)
        .filter(
            UserIntegration.user_id == owner["id"],
            UserIntegration.provider == "google",
        )
        .first()
    )
    assert google is not None
    assert google.status == "active"
    assert google.refresh_token_encrypted == "encrypted-google-token"


def test_manual_attendance_feature_still_works(client, owner):
    """The existing manual attendance CRUD must be unaffected by the connector."""
    headers = auth(owner["token"])
    r = client.post(
        "/api/attendance",
        json={"course": "Manual Course", "date": "2026-10-01T09:00:00", "status": "present"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    listed = client.get("/api/attendance", headers=headers).json()
    assert any(item["course"] == "Manual Course" for item in listed)
    listed = client.get("/api/attendance", headers=headers).json()
    assert any(item["course"] == "Manual Course" for item in listed)
    assert client.get("/api/attendance/stats", headers=headers).status_code == 200

