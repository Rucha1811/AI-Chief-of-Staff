from tests.conftest import auth


def test_status_works_without_token(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["authenticated"] is False
    assert body["user"] is None


def test_first_register_is_owner(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "owner@aichief.test", "password": "ownerpassword123"},
    )
    assert r.status_code == 201
    body = r.json()
    assert "owner" in body["roles"]
    assert "manage_users" in body["permissions"]
    assert "manage_integrations" in body["permissions"]
    assert len(body["permissions"]) == 30


def test_second_register_needs_token(client, owner):
    r = client.post(
        "/api/auth/register",
        json={"email": "anon@aichief.test", "password": "anonpassword123"},
    )
    assert r.status_code == 401


def test_second_register_with_owner_token_is_member(client, owner):
    r = client.post(
        "/api/auth/register",
        json={"email": "member@aichief.test", "password": "memberpassword123"},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["roles"] == ["member"]
    assert len(body["permissions"]) == 14
    assert "manage_users" not in body["permissions"]
    assert "manage_integrations" not in body["permissions"]


def test_duplicate_register_is_409(client, owner):
    r = client.post(
        "/api/auth/register",
        json={"email": owner["email"], "password": "whatever123"},
    )
    assert r.status_code == 409


def test_login_bad_password_is_401(client, owner):
    r = client.post(
        "/api/auth/login",
        json={"email": "owner@aichief.test", "password": "not-the-password"},
    )
    assert r.status_code == 401


def test_login_good_password_returns_token_with_owner(client, owner):
    r = client.post(
        "/api/auth/login",
        json={"email": "owner@aichief.test", "password": "ownerpassword123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["user"]["id"] == owner["id"]


def test_legacy_password_login_maps_to_earliest_user(client, owner):
    r = client.post("/api/auth/login", json={"password": "aichief"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    me = client.get("/api/auth/me", headers=auth(body["token"]))
    assert me.status_code == 200
    assert me.json()["id"] == owner["id"]


def test_me_requires_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_returns_profile_and_permissions(client, owner):
    r = client.get("/api/auth/me", headers=auth(owner["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == owner["id"]
    assert "owner" in body["roles"]
    assert body["permissions"]


def test_member_sees_own_member_profile(client, owner, member):
    r = client.get("/api/auth/me", headers=auth(member["token"]))
    assert r.status_code == 200
    assert r.json()["id"] == member["id"]
    assert r.json()["roles"] == ["member"]