import os
from pathlib import Path

import pytest

if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost:5432/postgres"

BASE_DIR = Path(__file__).resolve().parent.parent


def _build_test_url(base_url: str, db_name: str) -> str:
    head, _sep, _db = base_url.rpartition("/")
    return f"{head}/{db_name}"


def _reset_database(admin_url: str, db_name: str) -> None:
    from sqlalchemy import create_engine, text

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin.dispose()


TEST_DB_NAME = "ai_chief_test"


def _prepare_test_db() -> str:
    from app.core.config import settings

    test_url = _build_test_url(settings.DATABASE_URL, TEST_DB_NAME)
    admin_url = _build_test_url(settings.DATABASE_URL, "postgres")
    _reset_database(admin_url, TEST_DB_NAME)
    os.environ["DATABASE_URL"] = test_url
    return test_url


# NOTE: order matters. We must point DATABASE_URL at the test database
# before importing any application module.
try:
    from app.core.config import settings as _settings

    test_url = _prepare_test_db()

    # Fresh databases need the full schema before migrations can be applied
    # (0001 is a no-op stamp for the legacy DB). The migrations 0002-0004 are
    # idempotent, so create_all + upgrade converges to the same end state.
    import app.models  # noqa: F401
    from app.core.database import Base, engine

    Base.metadata.create_all(engine)

    from alembic import command
    from alembic.config import Config

    _cfg = Config(str(BASE_DIR / "alembic.ini"))
    _cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    command.upgrade(_cfg, "head")

    # 0002 seeds a bootstrap owner (admin@aichief.local). Drop it so the
    # test flow's first /api/auth/register becomes the owner, matching the
    # production first-user bootstrap.
    from app.core.database import SessionLocal
    from sqlalchemy import text

    _s = SessionLocal()
    try:
        _s.execute(text("DELETE FROM users"))
        _s.commit()
    finally:
        _s.close()

    from app.core.database import get_db
    from fastapi.testclient import TestClient

    from app.main import app
except Exception:
    raise


@pytest.fixture(scope="session")
def db_engine():
    return engine


# Seeded reference tables + alembic bookkeeping must survive the per-test
# wipe. user_roles is a dependent of users (cleared via TRUNCATE CASCADE).
_CATALOG_TABLES = {"alembic_version", "roles", "permissions", "role_permissions", "user_roles"}


def _wipe_tables(db_engine):
    from sqlalchemy import inspect, text

    with db_engine.connect() as conn:
        names = [n for n in inspect(db_engine).get_table_names() if n not in _CATALOG_TABLES]
        if not names:
            return
        conn.execute(text(f"TRUNCATE TABLE {', '.join(names)} RESTART IDENTITY CASCADE"))
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_db(db_engine):
    _wipe_tables(db_engine)
    yield
    _wipe_tables(db_engine)


@pytest.fixture
def db(db_engine):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db):
    # Do NOT enter the TestClient context: that would fire the app startup
    # event (create_all + background sync threads). Requests work fine here
    # and only our overridden get_db is used.
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(get_db, None)


def _register(client, email, password, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post(
        "/api/auth/register", json={"email": email, "password": password}, headers=headers
    )
    return r


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.fixture
def owner(client) -> dict:
    r = _register(client, "owner@aichief.test", "ownerpassword123")
    assert r.status_code == 201, r.text
    owner_id = r.json()["id"]
    r = _login(client, "owner@aichief.test", "ownerpassword123")
    assert r.status_code == 200, r.text
    return {"id": owner_id, "token": r.json()["token"], "email": "owner@aichief.test"}


@pytest.fixture
def member(client, owner) -> dict:
    r = _register(
        client, "member@aichief.test", "memberpassword123", token=owner["token"]
    )
    assert r.status_code == 201, r.text
    member_id = r.json()["id"]
    r = _login(client, "member@aichief.test", "memberpassword123")
    assert r.status_code == 200, r.text
    return {"id": member_id, "token": r.json()["token"], "email": "member@aichief.test"}


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}