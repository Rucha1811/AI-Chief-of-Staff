"""P1-2: the Gmail account-email cache is scoped per user.

The old code cached one global profile email (memory + cache/account_email.json
file), so User A's resolved address could leak into User B's UI. Both caches
are now keyed by user_id, and the integration's own account_id is the preferred
source.
"""

import asyncio
import json
import pathlib

from tests.conftest import auth  # noqa: F401


def _make_profile_service(email):
    class _Exec:
        def __init__(self, payload):
            self._payload = payload

        def execute(self):
            return self._payload

    class _Users:
        def getProfile(self, userId="me"):
            return _Exec({"emailAddress": email})

    class _Service:
        def users(self):
            return _Users()

    return _Service()


def _monkeypatch_offline_access(monkeypatch, emails_by_user, tmp_path):
    """Route get_account_email through the per-user service + cache path
    (integration_row returns nothing) so we exercise memory and disk caches.

    The disk cache dir is redirected to a tmp path so the tests never write
    into the real ``backend/cache/`` working-tree directory (which would both
    pollute the repo and risk serving a stale account email in a later run).
    """
    from app.services import gmail_service

    async def _fake_user_service(db=None, user_id=None):
        return _make_profile_service(emails_by_user[user_id])

    monkeypatch.setattr(
        gmail_service,
        "integration_row",
        lambda db, provider="google", user_id=None: None,
    )
    monkeypatch.setattr(gmail_service, "_get_user_service", _fake_user_service)
    monkeypatch.setattr(gmail_service, "_PROFILE_CACHE_DIR", str(tmp_path))
    gmail_service._profile_cache.clear()
    return gmail_service


def test_account_email_cache_is_scoped_per_user(db, owner, member, monkeypatch, tmp_path):
    from app.services import gmail_service

    _monkeypatch_offline_access(
        monkeypatch,
        {
            owner["id"]: "owner-google@example.com",
            member["id"]: "member-google@example.com",
        },
        tmp_path,
    )

    owner_email = asyncio.run(
        gmail_service.get_account_email(db=db, user_id=owner["id"])
    )
    member_email = asyncio.run(
        gmail_service.get_account_email(db=db, user_id=member["id"])
    )

    assert owner_email == "owner-google@example.com"
    assert member_email == "member-google@example.com"
    assert owner_email != member_email
    # in-memory cache must be keyed by user, never shared
    assert gmail_service._profile_cache[owner["id"]]["email"] == owner_email
    assert gmail_service._profile_cache[member["id"]]["email"] == member_email


def test_cached_value_is_never_served_to_other_user(db, owner, member, monkeypatch, tmp_path):
    from app.services import gmail_service

    _monkeypatch_offline_access(
        monkeypatch,
        {
            owner["id"]: "owner-google@example.com",
            member["id"]: "member-google@example.com",
        },
        tmp_path,
    )

    # Resolve owner's address first, then member: member must NOT get it.
    owner_email = asyncio.run(
        gmail_service.get_account_email(db=db, user_id=owner["id"])
    )
    member_email = asyncio.run(
        gmail_service.get_account_email(db=db, user_id=member["id"])
    )
    assert member_email == "member-google@example.com"
    assert member_email != owner_email


def test_disk_account_email_cache_is_scoped_per_user(db, owner, member, monkeypatch, tmp_path):
    from app.services import gmail_service

    _monkeypatch_offline_access(
        monkeypatch,
        {
            owner["id"]: "cache-owner@example.com",
            member["id"]: "cache-member@example.com",
        },
        tmp_path,
    )

    asyncio.run(gmail_service.get_account_email(db=db, user_id=owner["id"]))
    asyncio.run(gmail_service.get_account_email(db=db, user_id=member["id"]))

    owner_path = pathlib.Path(gmail_service._profile_cache_path(owner["id"]))
    member_path = pathlib.Path(gmail_service._profile_cache_path(member["id"]))

    assert owner_path.exists(), "owner cache file not written"
    assert member_path.exists(), "member cache file not written"
    assert owner_path != member_path
    assert (
        json.loads(owner_path.read_text())["email"]
        != json.loads(member_path.read_text())["email"]
    )


def test_integration_account_id_is_preferred_source(db, owner, monkeypatch):
    """If the user has an active google integration with account_id, that is
    returned without touching the (fake) profile API."""
    from app.models.user_integration import UserIntegration
    from app.services import gmail_service

    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            account_id="owner-account@example.com",
        )
    )
    db.commit()

    async def _fake_user_service(db=None, user_id=None):
        raise AssertionError("profile API should not be called")

    monkeypatch.setattr(gmail_service, "_get_user_service", _fake_user_service)

    email = asyncio.run(gmail_service.get_account_email(db=db, user_id=owner["id"]))
    assert email == "owner-account@example.com"


def test_disk_cache_never_writes_to_real_cache_dir(db, owner, monkeypatch, tmp_path):
    """The account-email disk cache must not write into the real
    ``backend/cache/`` working-tree directory during tests.

    A previous version of this test wrote ``account_email_{user_id}.json``
    into the real cache dir, which both polluted the repo and risked serving
    a stale account email on a later run (root-cause A of the account-mismatch
    investigation). Redirect ``_PROFILE_CACHE_DIR`` and assert the real dir is
    untouched.
    """
    from app.services import gmail_service

    real_cache_dir = pathlib.Path(gmail_service.__file__).parent.parent.parent / "cache"
    before = set(real_cache_dir.glob("account_email_*.json")) if real_cache_dir.exists() else set()

    _monkeypatch_offline_access(
        monkeypatch,
        {owner["id"]: "cache-isolation@example.com"},
        tmp_path,
    )

    asyncio.run(gmail_service.get_account_email(db=db, user_id=owner["id"]))

    after = set(real_cache_dir.glob("account_email_*.json")) if real_cache_dir.exists() else set()
    assert after == before, "test wrote account-email cache files into the real cache dir"
    # and the redirected tmp dir did receive the file
    assert list(tmp_path.glob("account_email_*.json")), "redirected cache dir should hold the file"