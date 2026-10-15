from tests.conftest import auth


def test_list_connections_requires_token(client):
    r = client.get("/api/integrations/user")
    assert r.status_code == 401


def test_create_connection_requires_token(client):
    r = client.post("/api/integrations/user", json={"provider": "gmail"})
    assert r.status_code == 401


def test_member_cannot_create_connection(client, owner, member):
    r = client.post(
        "/api/integrations/user",
        json={"provider": "gmail", "account_id": "member@test.local"},
        headers=auth(member["token"]),
    )
    assert r.status_code == 403


def test_owner_creates_and_only_owner_sees(client, owner, member):
    r = client.post(
        "/api/integrations/user",
        json={"provider": "gmail", "account_id": "owner@example.com"},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "gmail"

    owner_list = client.get("/api/integrations/user", headers=auth(owner["token"]))
    assert owner_list.status_code == 200
    assert any(c["provider"] == "gmail" for c in owner_list.json())

    member_list = client.get("/api/integrations/user", headers=auth(member["token"]))
    assert member_list.status_code == 200
    assert member_list.json() == []


def test_member_cannot_delete_owner_connection(client, owner, member):
    client.post(
        "/api/integrations/user",
        json={"provider": "gmail"},
        headers=auth(owner["token"]),
    )
    r = client.delete(
        "/api/integrations/user/gmail", headers=auth(member["token"])
    )
    assert r.status_code in (403, 404)

    still_there = client.get("/api/integrations/user", headers=auth(owner["token"]))
    assert any(c["provider"] == "gmail" for c in still_there.json())


def test_owner_can_delete_own_connection(client, owner):
    client.post(
        "/api/integrations/user",
        json={"provider": "gmail"},
        headers=auth(owner["token"]),
    )
    r = client.delete("/api/integrations/user/gmail", headers=auth(owner["token"]))
    assert r.status_code == 204
    remaining = client.get("/api/integrations/user", headers=auth(owner["token"]))
    assert remaining.json() == []