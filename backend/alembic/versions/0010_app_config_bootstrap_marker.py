"""app_config for one-shot security markers

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-04 09:00:00.000000

Step P24B-06 (first-user bootstrap privilege escalation).

Creates ``app_config``, a tiny internal key/value table used for *durable*
one-shot security state - specifically the marker that says "the first owner
has been created" (see app/services/bootstrap.py).

Why a table and not a check on ``users``: an empty ``users`` table is a
*re-openable* state. Deleting the owner row (by an operator, by a bad restore,
by a test fixture) would otherwise hand the next anonymous caller the ``owner``
role and therefore ``manage_users``. The marker is written in the same
transaction as the user insert, so it cannot exist without the owner and cannot
be missing while the owner exists.

Why the invariant is enforced in the application rather than with a unique
index: PostgreSQL forbids subqueries in an index predicate, so "at most one row
carrying the owner role" cannot be expressed as an index over user_roles
without adding a denormalized column or a trigger. The transactional claim
(``INSERT ... ON CONFLICT DO NOTHING`` in the same transaction as the owner
insert) already makes a second owner impossible for every code path, including
concurrent ones, and does so without changing the meaning of any existing
column.

Scope and compatibility:
- New table only. No existing table, column, row or index is touched, so this is
  backward compatible with every existing database; an upgrade cannot fail on
  data.
- Idempotent: ``CREATE TABLE IF NOT EXISTS`` also matches a fresh ``create_all``
  schema built from the model, which declares the same table.
- Downgrade drops the table, which only discards the marker. That would re-open
  first-owner creation on a database that still has users - harmless for the
  app (the endpoint requires ``manage_users`` once any user exists) but it is
  why a downgrade should be a deliberate operator action, not a routine one.

No secrets, tokens or user data are ever stored here; only marker keys and a
non-sensitive owner_user_id for audit.
"""
revision: str = '0010'
down_revision: str = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key VARCHAR(128) PRIMARY KEY,
            value TEXT,
            claimed_at TIMESTAMP NOT NULL DEFAULT now(),
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    # Removes the one-shot bootstrap marker with the table. See the docstring:
    # after a downgrade, an empty `users` table is the only thing standing
    # between an anonymous caller and the owner role.
    op.execute("DROP TABLE IF EXISTS app_config")
