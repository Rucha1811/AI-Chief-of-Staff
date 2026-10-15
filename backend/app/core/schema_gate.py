"""Production startup schema gate (Phase D4).

Alembic is the only authority on the schema. Before this module existed,
importing ``app.main`` unconditionally ran ``Base.metadata.create_all`` and an
ad-hoc ``ALTER TABLE`` pass - a production process silently creating tables
and adding columns behind the migration history's back. That is exactly how a
deployment ends up running code against a schema no migration describes.

This module is READ-ONLY by construction:

* it reads the repository's migration history (``alembic.ini`` + versions);
* it reads ``alembic_version`` from the database;
* it raises when the two disagree.

It never calls ``alembic command.upgrade``, never issues DDL, never opens a
write transaction. Startup fails loudly and safely instead of "fixing" the
schema, and the operator applies the migration deliberately
(``alembic upgrade head``) after reviewing what it changes.

Why multiple heads are fatal
----------------------------
Two heads means the migration history has two unmerged branches. Picking either
one silently would leave the running code and the repository disagreeing about
what the schema is, so the gate refuses rather than guess.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional, Sequence

logger = logging.getLogger("ai-chief.schema")

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = _BACKEND_DIR / "alembic"

# Environments where the startup gate applies. Development and testing keep
# their bootstrap conveniences; anything that resembles a real deployment must
# have its schema brought up to head out-of-band.
GATE_ENVIRONMENTS = frozenset({"production", "staging"})


class SchemaGateError(RuntimeError):
    """Raised at startup when the database schema is not exactly at head.

    Deliberately a hard failure: serving requests against a schema that does
    not match the deployed code is how data gets written to columns that do
    not exist (or silently skip columns that do).
    """


def _script_directory():
    """Build the Alembic script directory for this checkout (no DB access)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    # Resolved from this module rather than the CWD so the gate behaves the
    # same whether the process starts in the repo, in /app, or under launchd.
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    return ScriptDirectory.from_config(config)


def repository_heads(script=None) -> list[str]:
    """Sorted migration heads of this repository. Read-only."""
    script = script if script is not None else _script_directory()
    return sorted(script.get_heads())


def known_revisions(script=None) -> set[str]:
    """Every revision id present in this repository. Read-only."""
    script = script if script is not None else _script_directory()
    return {revision.revision for revision in script.walk_revisions()}


def database_revision(engine) -> Optional[str]:
    """Read ``alembic_version`` from the database without writing anything.

    Returns ``None`` when the table or the row is absent (a database that has
    never been migrated) and raises :class:`SchemaGateError` when the database
    cannot be interrogated at all, so an unreachable database is never
    misreported as "unmigrated".

    Error text deliberately carries only the exception type name: a driver
    message can embed the connection string, which carries credentials.
    """
    from sqlalchemy import inspect, text

    try:
        if "alembic_version" not in inspect(engine).get_table_names():
            return None
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).first()
    except SchemaGateError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
        raise SchemaGateError(
            "could not read the Alembic revision from the database "
            f"({type(exc).__name__})"
        ) from None
    return row[0] if row else None


def evaluate_schema_policy(
    heads: Sequence[str],
    db_revision: Optional[str],
    known: Iterable[str],
) -> tuple[bool, str]:
    """Pure decision function: does the database match the repository?

    Kept free of I/O so every branch (behind, unknown, branched, unmigrated)
    can be proven in a test without touching a real database.
    """
    known_revs = set(known)
    ordered = sorted(heads)

    if not ordered:
        return False, "the repository has no migration head (no migration scripts found)"
    if len(ordered) > 1:
        return False, (
            "the repository has multiple migration heads "
            f"({', '.join(ordered)}); merge the branches before starting"
        )

    head = ordered[0]
    if db_revision is None:
        return False, (
            "the database has no Alembic revision recorded (it has never been "
            f"migrated); expected {head}"
        )
    if db_revision not in known_revs:
        return False, (
            f"the database is at revision {db_revision!r}, which does not exist "
            "in this repository's migration history"
        )
    if db_revision != head:
        return False, (
            f"the database is at revision {db_revision} but the repository head "
            f"is {head}; startup never applies migrations automatically - run "
            "'alembic upgrade head' deliberately after reviewing the migration"
        )
    return True, f"database revision {db_revision} matches repository head {head}"


def enforce_schema_gate(engine=None) -> str:
    """Refuse to start unless the database is exactly at the migration head.

    Returns the accepted revision. Raises :class:`SchemaGateError` otherwise.
    Performs no writes of any kind.
    """
    if engine is None:
        from app.core.database import engine as _engine

        engine = _engine

    script = _script_directory()
    heads = repository_heads(script)
    revisions = known_revisions(script)
    revision = database_revision(engine)

    ok, reason = evaluate_schema_policy(heads, revision, revisions)
    if not ok:
        raise SchemaGateError(f"Schema gate refused to start: {reason}")

    logger.info("schema gate: %s", reason)
    return revision or ""
