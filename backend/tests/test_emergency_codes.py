"""P1-3: emergency codes are bound to exactly one user.

A code generated for User A can never authenticate User B, codes are
single-use and expire, and login requires the account email alongside the code.

NOTE on login rate-limiting: the auth layer locks an IP after 5 failures. Each
test that triggers a failed login finishes with a successful password login,
which resets the failure streak, so neighbouring tests never trip the lockout.
"""

from app.services.emergency_codes import consume_emergency_code, generate_emergency_code


def _emergency_login(client, email, code):
    return client.post(
        "/api/auth/login", json={"email": email, "emergency_code": code}
    )


def _password_login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_emergency_login_with_bound_code_succeeds_once(client, db, owner):
    code = generate_emergency_code(owner["id"], db=db)

    r = _emergency_login(client, owner["email"], code)
    assert r.status_code == 200
    assert r.json()["user"]["id"] == owner["id"]

    # single-use: the same code cannot be consumed twice
    r2 = _emergency_login(client, owner["email"], code)
    assert r2.status_code == 401

    _password_login(client, owner["email"], "ownerpassword123")


def test_emergency_code_cannot_authenticate_other_user(client, db, owner, member):
    code = generate_emergency_code(member["id"], db=db)

    # A code meant for the member must not log in the owner.
    r = _emergency_login(client, owner["email"], code)
    assert r.status_code == 401
    assert _password_login(client, owner["email"], "ownerpassword123").status_code == 200


def test_emergency_code_requires_account_email(client, db, owner):
    code = generate_emergency_code(owner["id"], db=db)

    r = client.post("/api/auth/login", json={"emergency_code": code})
    assert r.status_code == 401

    _password_login(client, owner["email"], "ownerpassword123")


def test_wrong_emergency_code_fails(client, owner):
    r = _emergency_login(client, owner["email"], "definitely-not-a-real-code")
    assert r.status_code == 401
    _password_login(client, owner["email"], "ownerpassword123")


def test_expired_emergency_code_is_rejected(client, db, owner):
    code = generate_emergency_code(owner["id"], ttl_hours=-1, db=db)
    r = _emergency_login(client, owner["email"], code)
    assert r.status_code == 401
    _password_login(client, owner["email"], "ownerpassword123")


def test_consume_is_scoped_to_the_bound_user(db, owner, member):
    code = generate_emergency_code(owner["id"], db=db)

    assert consume_emergency_code(code, member["id"], db) is False
    assert consume_emergency_code(code, None, db) is False
    assert consume_emergency_code(code, owner["id"], db) is True
    # already consumed -> second attempt fails, even for the right user
    assert consume_emergency_code(code, owner["id"], db) is False


def test_codes_for_different_users_are_distinct(db, owner, member):
    a = generate_emergency_code(owner["id"], db=db)
    b = generate_emergency_code(member["id"], db=db)
    assert a and b
    assert a != b
    assert generate_emergency_code(owner["id"], db=db) != a


def test_generated_code_is_not_stored_in_plaintext(db, owner):
    from app.models.emergency_code import EmergencyCode

    code = generate_emergency_code(owner["id"], db=db)
    row = (
        db.query(EmergencyCode)
        .filter(EmergencyCode.user_id == owner["id"])
        .order_by(EmergencyCode.id.desc())
        .first()
    )
    assert row is not None
    assert row.user_id == owner["id"]
    assert row.code_hash != code
    assert code not in row.code_hash