"""Phase D - D4: read-only production schema gate.

Before this module existed, importing ``app.main`` ran
``Base.metadata.create_all`` and an ad-hoc ``ALTER TABLE`` pass in EVERY
environment - so a production process could silently create tables and add
columns behind Alembic's back. The gate replaces that with a comparison:

    repository migration head   vs   alembic_version in the database

and refuses to start when they differ, having written nothing.

Every database used in this file is a throwaway SQLite file under pytest's
tmp_path. Nothing here touches a server, and no Alembic upgrade is ever
issued.
"""

import pytest
from sqlalchemy import create_engine, inspect

from app.core import schema_gate
from app.core.schema_gate import SchemaGateError, evaluate_schema_policy


@pytest.fixture
def sqlite(tmp_path):
    """A throwaway database. Nothing in this file talks to a server."""
    engine = create_engine(f"sqlite:///{tmp_path / 'gate.db'}")
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. the pure decision function
# ---------------------------------------------------------------------------


def test_a_database_at_head_is_accepted():
    ok, reason = evaluate_schema_policy(["0010"], "0010", {"0008", "0009", "0010"})
    assert ok is True
    assert "0010" in reason


def test_a_database_behind_head_is_rejected():
    ok, reason = evaluate_schema_policy(["0010"], "0009", {"0009", "0010"})
    assert ok is False
    assert "0009" in reason and "0010" in reason
    assert "alembic upgrade head" in reason


def test_a_database_ahead_of_the_repository_is_rejected():
    ok, reason = evaluate_schema_policy(["0010"], "0011", {"0010"})
    assert ok is False
    assert "0011" in reason


def test_an_unknown_revision_is_rejected():
    ok, reason = evaluate_schema_policy(["0010"], "totally-made-up", {"0010"})
    assert ok is False
    assert "totally-made-up" in reason
    assert "migration history" in reason


def test_a_never_migrated_database_is_rejected():
    ok, reason = evaluate_schema_policy(["0010"], None, {"0010"})
    assert ok is False
    assert "never been migrated" in reason


def test_multiple_heads_are_rejected_rather_than_guessed_at():
    ok, reason = evaluate_schema_policy(["0010", "0012"], "0010", {"0010", "0012"})
    assert ok is False
    assert "multiple migration heads" in reason
    assert "merge" in reason


def test_a_repository_with_no_migrations_is_rejected():
    ok, reason = evaluate_schema_policy([], None, set())
    assert ok is False
    assert "no migration head" in reason


# ---------------------------------------------------------------------------
# 2. this repository's actual history
# ---------------------------------------------------------------------------


def test_the_repository_has_exactly_one_head():
    heads = schema_gate.repository_heads()
    assert len(heads) == 1, f"resolve the branched migration history: {heads}"


def test_the_head_is_a_revision_this_repository_knows_about():
    heads = schema_gate.repository_heads()
    assert set(heads) <= schema_gate.known_revisions()


def test_the_production_database_revision_0009_would_be_rejected():
    """The live deployment sits at alembic revision 0009 while the repository
    head is 0010. That is precisely the state the gate exists for: startup
    must refuse rather than apply migration 0010 on its own.
    """
    heads = schema_gate.repository_heads()
    known = schema_gate.known_revisions()
    assert "0009" in known, "0009 must be part of this repository's history"
    assert heads != ["0009"], "the repository head must have moved past 0009"

    ok, reason = evaluate_schema_policy(heads, "0009", known)
    assert ok is False
    assert "0009" in reason and heads[0] in reason


def test_a_known_non_head_revision_is_always_rejected():
    heads = schema_gate.repository_heads()
    known = schema_gate.known_revisions()
    non_heads = sorted(known - set(heads))
    assert non_heads, "expected at least one historical revision"
    for revision in non_heads:
        ok, _reason = evaluate_schema_policy(heads, revision, known)
        assert ok is False, f"revision {revision} must not be accepted"


# ---------------------------------------------------------------------------
# 3. reading the database revision (read-only)
# ---------------------------------------------------------------------------


def test_an_unmigrated_database_reports_no_revision(sqlite):
    assert schema_gate.database_revision(sqlite) is None


def test_a_migrated_database_reports_its_revision(sqlite):
    from sqlalchemy import text

    with sqlite.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0009')"))
    assert schema_gate.database_revision(sqlite) == "0009"


def test_reading_the_revision_creates_nothing(sqlite):
    schema_gate.database_revision(sqlite)
    assert inspect(sqlite).get_table_names() == []


def test_an_unreadable_database_fails_closed_with_a_type_only_message(tmp_path):
    """An unreachable database must not be reported as 'unmigrated', and the
    message must carry only the exception type - a driver message can embed
    the connection string, which carries credentials."""
    engine = create_engine(f"sqlite:///{tmp_path}/no_such_directory_here/x.db")
    try:
        with pytest.raises(SchemaGateError) as excinfo:
            schema_gate.database_revision(engine)
    finally:
        engine.dispose()
    message = str(excinfo.value)
    assert "could not read the Alembic revision" in message
    assert "sqlite:///" not in message


# ---------------------------------------------------------------------------
# 4. the gate itself: refuses without writing
# ---------------------------------------------------------------------------


def test_the_gate_accepts_a_database_that_is_exactly_at_head(sqlite):
    from sqlalchemy import text

    head = schema_gate.repository_heads()[0]
    with sqlite.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head})

    assert schema_gate.enforce_schema_gate(sqlite) == head


def test_the_gate_refuses_a_database_at_0009(sqlite):
    from sqlalchemy import text

    with sqlite.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0009')"))

    with pytest.raises(SchemaGateError) as excinfo:
        schema_gate.enforce_schema_gate(sqlite)
    assert "0009" in str(excinfo.value)
    assert schema_gate.repository_heads()[0] in str(excinfo.value)


def test_the_gate_refuses_a_never_migrated_database_without_creating_one(sqlite):
    with pytest.raises(SchemaGateError) as excinfo:
        schema_gate.enforce_schema_gate(sqlite)
    assert "never been migrated" in str(excinfo.value)
    # The whole point: not a single table was created on the way out.
    assert inspect(sqlite).get_table_names() == []


def test_the_gate_is_wired_into_the_environments_that_matter():
    assert schema_gate.GATE_ENVIRONMENTS == frozenset({"production", "staging"})
    assert "development" not in schema_gate.GATE_ENVIRONMENTS
    assert "testing" not in schema_gate.GATE_ENVIRONMENTS


# ---------------------------------------------------------------------------
# 5. main.py: production mutates nothing, development keeps its conveniences
# ---------------------------------------------------------------------------


def test_production_startup_creates_and_migrates_nothing(monkeypatch, tmp_path):
    import app.main as main_module

    calls = []
    monkeypatch.setattr(
        main_module.Base.metadata, "create_all", lambda **_kw: calls.append("create_all")
    )
    monkeypatch.setattr(main_module, "_run_migrations", lambda *a, **k: calls.append("migrate"))
    monkeypatch.setattr(main_module.settings, "ENVIRONMENT", "production")

    engine = create_engine(f"sqlite:///{tmp_path}/production_boot.db")
    try:
        with pytest.raises(SchemaGateError):
            main_module.apply_startup_schema_policy(target_engine=engine)
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()

    assert calls == [], "production startup must not touch the schema"


def test_staging_startup_is_gated_too(monkeypatch, tmp_path):
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "ENVIRONMENT", "staging")
    engine = create_engine(f"sqlite:///{tmp_path}/staging_boot.db")
    try:
        with pytest.raises(SchemaGateError):
            main_module.apply_startup_schema_policy(target_engine=engine)
    finally:
        engine.dispose()


def test_development_startup_keeps_its_conveniences(monkeypatch):
    import app.main as main_module

    calls = []
    monkeypatch.setattr(
        main_module.Base.metadata, "create_all", lambda **_kw: calls.append("create_all")
    )
    monkeypatch.setattr(main_module, "_run_migrations", lambda *a, **k: calls.append("migrate"))
    monkeypatch.setattr(main_module.settings, "ENVIRONMENT", "development")

    main_module.apply_startup_schema_policy()

    assert calls == ["create_all", "migrate"]


def test_the_gate_never_reaches_for_alembics_migrating_commands():
    """Static guarantee: the gate reads the migration history, it never runs
    it. (Behavioural proof is above: after a refusal the database still has
    no tables at all.)"""
    from pathlib import Path

    source = Path(schema_gate.__file__).read_text()
    assert "from alembic import command" not in source
    assert "alembic.command" not in source
    assert "command.upgrade(" not in source
    assert ".begin()" not in source, "the gate must not open a write transaction"
