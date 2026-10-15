"""CHARUSAT student eligibility, identity verification and connector ownership.

The attendance feature is student-only and is unlocked exclusively by an
identity provider's *verified-email* claim. The requirements these tests guard:

1. Identity - the domain in ``users.email`` (user-editable at registration) is
   never sufficient; only a provider-verified institutional address qualifies,
   and non-CHARUSAT accounts keep working exactly as before.
2. Association - attendance is always the authenticated user's own. There is no
   identifier field through which one student could request another's rows, and
   every query/endpoint is scoped to ``user.id``.
3. Separation - eligibility comes from identity verification, not from a Google
   data integration.

The real portal is never contacted: the import path parses saved HTML.
"""

from app.models.user import User
from app.services import charusat_identity
from app.core.config import settings
from tests.conftest import auth

STUDENT_PASSWORD = "studentpassword123"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _transient_user(email="x@y.test", status="active", email_verified=False, verified_email=None) -> User:
    user = User(email=email, password_hash="x", status=status)
    setattr(user, "email_verified", email_verified)
    setattr(user, "verified_email", verified_email)
    return user


def _register(client, email, password=STUDENT_PASSWORD, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post(
        "/api/auth/register", json={"email": email, "password": password}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _login(client, email, password=STUDENT_PASSWORD) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _student(client, email, token=None) -> dict:
    uid = _register(client, email, token=token)
    bearer = _login(client, email)
    return {"id": uid, "token": bearer, "email": email}


def _mark_verified(db, user_id: int, email: str) -> None:
    """Simulate a provider having confirmed this address.

    Production writes these columns from Google's ``email_verified`` claim via
    ``charusat_identity.record_verified_identity``; a direct assignment keeps
    the API tests focused on enforcement rather than on the OAuth dance.
    """
    user = db.get(User, user_id)
    setattr(user, "email_verified", True)
    user.verified_email = email
    db.commit()


def _drive_google_login(client, monkeypatch, *, email, verified=True, name="Google User"):
    """Complete the Google *login* callback with a controlled verified claim."""
    from app.services import google_auth
    from app.services.auth_state import AuthStateStore

    class _FakeFlow:
        credentials = type("Creds", (), {"expiry": None})()

        def __init__(self, *args, **kwargs):
            pass

        def fetch_token(self, code):
            return None

    monkeypatch.setattr(google_auth, "_oauth_flow", lambda *a, **k: _FakeFlow())
    monkeypatch.setattr(
        google_auth,
        "_account_identity",
        lambda creds, id_token=None: {
            "email": email,
            "name": name,
            "google_sub": "sub-" + email,
            "email_verified": verified,
        },
    )
    monkeypatch.setattr(AuthStateStore, "renew_expiry", staticmethod(lambda **k: None))

    state = "login_" + "a" * 32
    return client.get(
        "/api/integrations/google/callback",
        params={"code": "google-code", "state": state},
        cookies={"oauth_state": state},
        follow_redirects=False,
    )


def attendance_page(rows: str) -> str:
    return f"""
    <html><body><form id="form1">
      <table id="gvAttendance" class="table table-bordered">
        <tr><th>Sr No</th><th>Subject Code</th><th>Subject</th><th>Lectures Held</th>
            <th>Lectures Attended</th><th>Attendance %</th></tr>
        {rows}
      </table>
    </form></body></html>
    """


ROW_A = "<tr><td>1</td><td>CE301</td><td>Data Structures</td><td>48</td><td>40</td><td>83.3</td></tr>"
ROW_B = "<tr><td>1</td><td>CE401</td><td>Compilers</td><td>40</td><td>20</td><td>50</td></tr>"


# --------------------------------------------------------------------------- #
# Unit: eligibility rules (no DB)
# --------------------------------------------------------------------------- #
def test_configured_domain_default():
    assert settings.charusat_institutional_domain == "charusat.edu.in"


def test_email_domain_alone_is_not_eligibility():
    info = charusat_identity.eligibility(
        _transient_user(email="24dcs025@charusat.edu.in")
    )
    assert info["eligible"] is False
    assert info["reason"] == "not_verified"


def test_verified_institutional_identity_is_eligible():
    user = _transient_user(
        email="24dcs025@charusat.edu.in",
        verified_email="24dcs025@charusat.edu.in",
    )
    setattr(user, "email_verified", True)
    info = charusat_identity.eligibility(user)
    assert info["eligible"] is True
    assert info["reason"] == "ok"
    assert info["masked_email"] == "24d***@charusat.edu.in"


def test_verified_non_institutional_identity_is_not_eligible():
    info = charusat_identity.eligibility(
        _transient_user(
            email="someone@gmail.com",
            email_verified=True,
            verified_email="someone@gmail.com",
        )
    )
    assert info["eligible"] is False
    assert info["reason"] == "wrong_domain"


def test_verified_flag_without_address_is_not_eligible():
    info = charusat_identity.eligibility(_transient_user(email_verified=True))
    assert info["eligible"] is False
    assert info["reason"] == "not_verified"


def test_inactive_verified_account_is_not_eligible():
    info = charusat_identity.eligibility(
        _transient_user(
            status="revoked",
            email_verified=True,
            verified_email="24dcs025@charusat.edu.in",
        )
    )
    assert info["eligible"] is False
    assert info["reason"] == "account_inactive"


def test_mask_email_is_privacy_preserving():
    assert charusat_identity.mask_email("24dcs025@charusat.edu.in") == "24d***@charusat.edu.in"
    assert charusat_identity.mask_email("ab@charusat.edu.in") == "a***@charusat.edu.in"
    assert charusat_identity.mask_email("") is None
    assert charusat_identity.mask_email(None) is None


def test_is_institutional_email():
    assert charusat_identity.is_institutional_email("a@charusat.edu.in")
    assert not charusat_identity.is_institutional_email("a@gmail.com")
    assert not charusat_identity.is_institutional_email("")
    assert not charusat_identity.is_institutional_email(None)


# --------------------------------------------------------------------------- #
# record_verified_identity: the single write path
# --------------------------------------------------------------------------- #
def test_record_verified_identity_requires_provider_assertion(client, db):
    r = client.post(
        "/api/auth/register",
        json={"email": "rec@example.test", "password": "recpassword123"},
    )
    assert r.status_code == 201
    user = db.get(User, r.json()["id"])

    assert (
        charusat_identity.record_verified_identity(
            db, user, verified_email="rec@charusat.edu.in", email_verified=False
        )
        is False
    )
    db.refresh(user)
    assert not getattr(user, "email_verified", False)
    assert getattr(user, "verified_email", None) is None

    assert (
        charusat_identity.record_verified_identity(
            db, user, verified_email="rec@charusat.edu.in", email_verified=True
        )
        is True
    )
    db.refresh(user)
    assert getattr(user, "email_verified", False) is True
    assert user.verified_email == "rec@charusat.edu.in"


# --------------------------------------------------------------------------- #
# Google login writes the verified claim
# --------------------------------------------------------------------------- #
def test_google_login_records_verified_institutional_email(client, db, monkeypatch):
    email = "24dcs025@charusat.edu.in"
    uid = _register(client, email)
    user = db.get(User, uid)
    assert not getattr(user, "email_verified", False)  # nothing trusted yet

    res = _drive_google_login(client, monkeypatch, email=email, verified=True)
    assert res.status_code in (302, 307), res.text

    db.refresh(user)
    assert getattr(user, "email_verified", False) is True
    assert user.verified_email == email

    # Eligibility now holds for the account, and the API reflects it.
    body = client.get(
        "/api/attendance/egov", headers=auth(_login(client, email))
    ).json()
    assert body["eligible"] is True
    assert body["account"] == "24d***@charusat.edu.in"


def test_google_login_does_not_verify_when_provider_says_unverified(client, db, monkeypatch):
    email = "24dcs025@charusat.edu.in"
    uid = _register(client, email)

    res = _drive_google_login(client, monkeypatch, email=email, verified=False)
    assert res.status_code in (302, 307), res.text

    user = db.get(User, uid)
    db.refresh(user)
    assert not getattr(user, "email_verified", False)
    assert getattr(user, "verified_email", None) is None

    body = client.get(
        "/api/attendance/egov", headers=auth(_login(client, email))
    ).json()
    assert body["eligible"] is False
    assert body["eligibility_reason"] == "not_verified"


def test_google_login_non_institutional_email_is_still_ineligible(client, db, monkeypatch):
    email = "student@gmail.com"
    uid = _register(client, email)

    res = _drive_google_login(client, monkeypatch, email=email, verified=True)
    assert res.status_code in (302, 307), res.text

    user = db.get(User, uid)
    db.refresh(user)
    # The verified address is recorded truthfully, but it is not institutional.
    assert user.verified_email == email
    assert charusat_identity.eligibility(user)["eligible"] is False
    assert charusat_identity.eligibility(user)["reason"] == "wrong_domain"

    body = client.get(
        "/api/attendance/egov", headers=auth(_login(client, email))
    ).json()
    assert body["eligible"] is False
    assert body["eligibility_reason"] == "wrong_domain"


# --------------------------------------------------------------------------- #
# API enforcement: ineligible accounts cannot use the connector
# --------------------------------------------------------------------------- #
def test_users_email_domain_alone_does_not_unlock_the_connector(client):
    email = "24dcs099@charusat.edu.in"
    _register(client, email)
    body = client.get("/api/attendance/egov", headers=auth(_login(client, email))).json()
    assert body["eligible"] is False
    assert body["eligibility_reason"] == "not_verified"
    assert body["state"] == "not_eligible"
    assert body["records"] == []


def test_manual_only_account_mutations_are_403(client, owner):
    h = auth(owner["token"])  # a normal (non-CHARUSAT) account

    status = client.get("/api/attendance/egov", headers=h)
    assert status.status_code == 200
    body = status.json()
    assert body["eligible"] is False
    assert body["eligibility_reason"] == "not_verified"
    assert body["analysis"] is None

    resp = client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_A)},
        headers=h,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["eligible"] is False
    assert resp.json()["detail"]["reason"] == "not_verified"

    resp = client.post(
        "/api/attendance/egov/session",
        json={
            "session_cookie": "ASP.NET_SessionId=abc",
            "attendance_url": "https://support.charusat.edu.in/Egov/StudentAttendance.aspx",
        },
        headers=h,
    )
    assert resp.status_code == 403

    assert client.post("/api/attendance/egov/refresh", headers=h).status_code == 403

    # Disconnect stays available so any account can always clean up.
    assert client.delete("/api/attendance/egov", headers=h).status_code == 200


def test_verified_student_imports_and_sees_analysis(client, db):
    student = _student(client, "24dcs025@charusat.edu.in")
    _mark_verified(db, student["id"], student["email"])
    h = auth(student["token"])

    before = client.get("/api/attendance/egov", headers=h).json()
    assert before["eligible"] is True
    assert before["connected"] is False
    assert before["record_count"] == 0
    assert before["student_verified"] is False

    resp = client.post(
        "/api/attendance/egov/import",
        json={"html": attendance_page(ROW_A), "term": "Sem 5"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_count"] == 1
    assert body["student_verified"] is True

    analysis = body["analysis"]
    assert analysis["overall"]["percentage"] == 83.3
    assert analysis["subjects"][0]["subject_code"] == "CE301"
    assert analysis["subjects"][0]["status"] == "safe"


# --------------------------------------------------------------------------- #
# Ownership: two eligible students never see each other's attendance
# --------------------------------------------------------------------------- #
def test_two_students_receive_only_their_own_attendance(client, db):
    a = _student(client, "24dcs025@charusat.edu.in")
    b = _student(client, "24dcs026@charusat.edu.in", token=a["token"])
    _mark_verified(db, a["id"], a["email"])
    _mark_verified(db, b["id"], b["email"])

    assert (
        client.post(
            "/api/attendance/egov/import",
            json={"html": attendance_page(ROW_A)},
            headers=auth(a["token"]),
        ).status_code
        == 200
    )

    # B is eligible but has no data of their own: nothing bleeds over from A.
    b_status = client.get("/api/attendance/egov", headers=auth(b["token"])).json()
    assert b_status["eligible"] is True
    assert b_status["record_count"] == 0
    assert b_status["records"] == []

    assert (
        client.post(
            "/api/attendance/egov/import",
            json={"html": attendance_page(ROW_B)},
            headers=auth(b["token"]),
        ).status_code
        == 200
    )

    a_codes = {
        r["subject_code"]
        for r in client.get(
            "/api/attendance/egov", headers=auth(a["token"])
        ).json()["records"]
    }
    b_codes = {
        r["subject_code"]
        for r in client.get("/api/attendance/egov", headers=auth(b["token"])).json()[
            "records"
        ]
    }
    assert a_codes == {"CE301"}
    assert b_codes == {"CE401"}


def test_connect_request_carries_no_student_identifier():
    """There is no field through which a caller could name another student."""
    from app.api.egov_attendance import ImportRequest, SessionConnectRequest

    for forbidden in ("account_id", "student_id", "enrollment_no", "username"):
        assert field_not_present(SessionConnectRequest, forbidden)
        assert field_not_present(ImportRequest, forbidden)


def field_not_present(model, name) -> bool:
    return name not in getattr(model, "model_fields", {})


# --------------------------------------------------------------------------- #
# Regression: existing non-CHARUSAT accounts and admins are unaffected
# --------------------------------------------------------------------------- #
def test_non_charusat_account_keeps_manual_attendance_and_profile(client, owner):
    h = auth(owner["token"])

    created = client.post(
        "/api/attendance",
        json={"course": "Maths", "date": "2026-10-01T09:00:00", "status": "present"},
        headers=h,
    )
    assert created.status_code == 200, created.text

    stats = client.get("/api/attendance/stats", headers=h)
    assert stats.status_code == 200

    me = client.get("/api/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["email"] == "owner@aichief.test"


def test_admin_account_is_unaffected_by_the_gate(client, owner):
    h = auth(owner["token"])
    me = client.get("/api/auth/me", headers=h)
    assert me.status_code == 200
    assert "owner" in me.json()["roles"]

    # The connector reports eligibility rather than failing for an admin.
    resp = client.get("/api/attendance/egov", headers=h)
    assert resp.status_code == 200
    assert resp.json()["eligible"] is False

