"""CHARUSAT e-Governance attendance snapshot table

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-15 12:00:00.000000

Creates ``egov_attendance``: the per-user, subject-level snapshot of attendance
read from the CHARUSAT e-Governance portal. The connector is read-only and
kept fully separate from the manual ``attendance`` table, so adding it cannot
affect Gmail, Classroom, Calendar or the existing manual attendance feature.

The connection metadata (encrypted portal session cookie, status, last sync,
last error) is stored on the existing ``user_integrations`` row with
``provider = 'charusat_egov'`` - no new connection table is needed.

Idempotent for coexistence with create_all (the test schema and fresh installs
create the table from the model before Alembic runs), exactly like 0009-0011.
"""
revision: str = "0012"
down_revision: str = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS egov_attendance (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            term VARCHAR(64) NOT NULL DEFAULT '',
            subject_code VARCHAR(64) NOT NULL DEFAULT '',
            subject_name VARCHAR(256) NOT NULL DEFAULT '',
            classes_held INTEGER,
            classes_attended INTEGER,
            percentage DOUBLE PRECISION,
            source_updated_at TIMESTAMP WITHOUT TIME ZONE,
            synced_at TIMESTAMP WITHOUT TIME ZONE,
            source_reference VARCHAR(512),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ux_egov_attendance_subject
                UNIQUE (user_id, term, subject_code, subject_name)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_egov_attendance_user_id "
        "ON egov_attendance (user_id)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("DROP TABLE IF EXISTS egov_attendance")
