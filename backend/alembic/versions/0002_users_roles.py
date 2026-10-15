"""create users, roles, permissions, role_permissions, user_roles

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13 15:00:00.000000

Creates the identity + authorization tables (idempotent, safe on both a
live multi-user database and a fresh install where create_all has already
built the schema). Seeds the default permission catalog, three roles
(owner/admin/member) and a bootstrap owner user whose password follows
AUTH_PASSWORD (or the BOOTSTRAP_PASSWORD env override).
"""
import os
import secrets

revision: str = '0002'
down_revision: str = '0001'
branch_labels = None
depends_on = None


def _bootstrap_credentials():
    from app.core.config import settings
    from app.core.security import hash_password

    email = os.environ.get("BOOTSTRAP_EMAIL") or "admin@aichief.local"
    password = os.environ.get("BOOTSTRAP_PASSWORD") or settings.AUTH_PASSWORD
    if not password:
        password = secrets.token_urlsafe(24)
    return email.lower().strip(), hash_password(password)


_PERMISSIONS = [
    ("view_dashboard", "View dashboard/analytics"),
    ("read_email", "Read emails"),
    ("write_email", "Compose/send emails"),
    ("summarize_email", "AI summary/suggested replies"),
    ("manage_email_rules", "Email rules/filters"),
    ("read_event", "Read calendar events"),
    ("write_event", "Create/edit calendar events"),
    ("read_task", "Read tasks"),
    ("write_task", "Create/edit tasks"),
    ("manage_reminders", "Manage task reminders"),
    ("read_assignment", "Read assignments"),
    ("write_assignment", "Create/edit assignments"),
    ("read_exam", "Read exams/schedules"),
    ("write_exam", "Create/edit exams"),
    ("read_attendance", "Read attendance"),
    ("write_attendance", "Record attendance"),
    ("read_document", "Read documents"),
    ("write_document", "Upload/delete documents"),
    ("read_timetable", "Read timetable"),
    ("write_timetable", "Edit timetable"),
    ("manage_notifications", "Manage notifications"),
    ("send_notification", "Send push notifications"),
    ("view_briefing", "View daily briefing"),
    ("manage_integrations", "Manage connected service integrations"),
    ("use_whatsapp", "Use WhatsApp messaging"),
    ("manage_users", "Create/manage users"),
    ("manage_roles", "Assign roles"),
    ("manage_settings", "Change shared settings"),
    ("manage_system", "System administration"),
    ("view_audit_log", "View audit log"),
]

_ADMIN_EXCLUDED = {"manage_system"}

_MEMBER_ALLOWED = {
    "view_dashboard",
    "read_email",
    "summarize_email",
    "read_event",
    "read_task",
    "write_task",
    "manage_reminders",
    "read_assignment",
    "read_exam",
    "read_attendance",
    "read_document",
    "read_timetable",
    "view_briefing",
    "use_whatsapp",
}


def upgrade() -> None:
    op = None  # placeholder; SQL executed via raw statements below
    from alembic import op as _op

    op = _op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            email VARCHAR(512) NOT NULL,
            password_hash VARCHAR(512) NOT NULL,
            display_name VARCHAR(256),
            timezone VARCHAR(64) DEFAULT 'UTC' NOT NULL,
            preferences JSONB,
            status VARCHAR(16) DEFAULT 'active' NOT NULL,
            created_at TIMESTAMP DEFAULT now() NOT NULL,
            updated_at TIMESTAMP DEFAULT now() NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users (lower(email))"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(64) NOT NULL,
            name VARCHAR(128) NOT NULL,
            description TEXT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_roles_code ON roles (code)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS permissions (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(128) NOT NULL,
            description TEXT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_permissions_code ON permissions (code)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_permissions_permission_id "
        "ON role_permissions (permission_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, role_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_roles_role_id ON user_roles (role_id)"
    )

    # Seed role definitions (idempotent).
    op.execute(
        "INSERT INTO roles (code, name, description) VALUES "
        "('owner', 'Owner', 'Full control of the account and its users'), "
        "('admin', 'Admin', 'Administrative access without system-level controls'), "
        "('member', 'Member', 'Standard user with core read/write access') "
        "ON CONFLICT (code) DO NOTHING"
    )

    # Seed the permission catalog (idempotent).
    values = ",\n".join(
        f"('{code}', '{description.replace(chr(39), chr(39) + chr(39))}')"
        for code, description in _PERMISSIONS
    )
    op.execute(
        f"INSERT INTO permissions (code, description) VALUES {values} "
        "ON CONFLICT (code) DO NOTHING"
    )

    # Grant: owner -> all permissions.
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.code = 'owner'
        ON CONFLICT DO NOTHING
        """
    )
    # Grant: admin -> all except system administration.
    op.execute(
        f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.code = 'admin' AND p.code NOT IN ({', '.join(repr(c) for c in sorted(_ADMIN_EXCLUDED))})
        ON CONFLICT DO NOTHING
        """
    )
    # Grant: member -> core read/write set.
    op.execute(
        f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.code = 'member' AND p.code IN ({', '.join(repr(c) for c in sorted(_MEMBER_ALLOWED))})
        ON CONFLICT DO NOTHING
        """
    )

    # Bootstrap owner user (idempotent on the lower(email) unique index).
    email, password_hash = _bootstrap_credentials()
    op.execute(
        "INSERT INTO users (email, password_hash, display_name, timezone, status) "
        f"VALUES ({email!r}, {password_hash!r}, 'Administrator', 'UTC', 'active') "
        "ON CONFLICT (lower(email)) DO NOTHING"
    )

    # Attach the bootstrap user to the owner role.
    op.execute(
        "INSERT INTO user_roles (user_id, role_id) "
        f"SELECT u.id, r.id FROM users u CROSS JOIN roles r "
        f"WHERE u.email = lower({email!r}) AND r.code = 'owner' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("DROP TABLE IF EXISTS user_roles")
    op.execute("DROP TABLE IF EXISTS role_permissions")
    op.execute("DROP TABLE IF EXISTS permissions")
    op.execute("DROP TABLE IF EXISTS roles")
    op.execute("DROP TABLE IF EXISTS users")