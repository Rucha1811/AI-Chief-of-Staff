"""emergency codes user-scoped

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03 00:00:00.000000

Binds every emergency login code to exactly one user.

Legacy rows (created before users existed, with no user_id) are un-attributable
— we cannot guess who they belonged to. Rather than leave a global bypass or
silently assign ownership, this migration removes those rows. Reports the count
to the migration log. Idempotent for coexistence with create_all.
"""
revision: str = '0007'
down_revision: str = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        "ALTER TABLE emergency_codes ADD COLUMN IF NOT EXISTS user_id BIGINT"
    )
    # Legacy codes have no owner we could trust; they cannot be kept safely.
    op.execute("DELETE FROM emergency_codes WHERE user_id IS NULL")
    op.execute(
        "ALTER TABLE emergency_codes ALTER COLUMN user_id SET NOT NULL"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'emergency_codes_user_id_fkey'
            ) THEN
                ALTER TABLE emergency_codes
                ADD CONSTRAINT emergency_codes_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_emergency_codes_user_id "
        "ON emergency_codes (user_id)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("DROP INDEX IF EXISTS ix_emergency_codes_user_id")
    op.execute(
        "ALTER TABLE emergency_codes DROP CONSTRAINT IF EXISTS "
        "emergency_codes_user_id_fkey"
    )
    op.execute("ALTER TABLE emergency_codes ALTER COLUMN user_id DROP NOT NULL")
    op.execute("ALTER TABLE emergency_codes DROP COLUMN IF EXISTS user_id")