"""One-time SQLite -> PostgreSQL data migration.

The target DSN is read from the environment so no credential is stored in this
file. Either export a libpq DSN:

    PG_DSN="host=localhost port=5434 dbname=ai_chief user=postgres" \
        python scripts/migrate_sqlite_to_pg.py

or build one from discrete variables (PGPASSWORD, or ~/.pgpass, keeps the
password out of your shell history and out of this file):

    PGHOST=localhost PGPORT=5434 PGDATABASE=ai_chief PGUSER=postgres \
        python scripts/migrate_sqlite_to_pg.py

See .env.example / docker-compose.env.example for the variable names.
"""

import os
import sqlite3

import psycopg2

SQLITE_PATH = os.environ.get("SQLITE_PATH", "ai_chief.db")

_REQUIRED = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER")


def _pg_dsn() -> str:
    """Build the target DSN from the environment; never hardcode a password."""
    explicit = os.environ.get("PG_DSN")
    if explicit:
        return explicit
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "Set the PostgreSQL target before running this migration.\n"
            f"  missing: {', '.join(missing)}\n"
            "  either export PG_DSN=... or PGHOST/PGPORT/PGDATABASE/PGUSER "
            "(plus PGPASSWORD or ~/.pgpass for the password)"
        )
    return " ".join(
        f"{name.lower()}={os.environ[name]}"
        for name in _REQUIRED
    )

TABLES = [
    "assignments",
    "attendance",
    "daily_briefings",
    "documents",
    "emails",
    "emergency_codes",
    "events",
    "exams",
    "google_auth_state",
    "notifications",
    "push_subscriptions",
    "tasks",
    "timetable_entries",
]

src = sqlite3.connect(SQLITE_PATH)
src.row_factory = sqlite3.Row
dst = psycopg2.connect(_pg_dsn())
dst.autocommit = True

for table in TABLES:
    rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
    cols = list(rows[0].keys()) if rows else [
        r["name"] for r in src.execute(f'PRAGMA table_info("{table}")')
    ]

    with dst.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        pg_types = dict(cur.fetchall())
        missing = [c for c in cols if c not in pg_types]
        if missing:
            print(f"[SKIP] {table}: sqlite cols missing in pg: {missing}")
            continue

        cur.execute(f'TRUNCATE "{table}" RESTART IDENTITY CASCADE')

        col_list = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'

        count = 0
        for row in rows:
            values = []
            for c in cols:
                v = row[c]
                if pg_types[c] == "boolean" and v is not None:
                    v = bool(v)
                values.append(v)
            try:
                cur.execute(insert_sql, values)
                count += 1
            except Exception as e:
                print(f"[ERROR] {table} id={row['id'] if 'id' in cols else '?'}: {e}")
        print(f"[OK] {table}: {count}/{len(rows)} rows")

    if rows and "id" in cols:
        max_id = max(int(r["id"]) for r in rows)
        with dst.cursor() as cur:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), %s)",
                (max_id,),
            )
        print(f"    -> sequence advanced to {max_id}")

src.close()
dst.close()
print("migration complete")
