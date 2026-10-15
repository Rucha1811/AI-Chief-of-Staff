#!/usr/bin/env bash
# AI CHIEF backups: Postgres (all AI data, including the encrypted per-user
# Google OAuth tokens) + n8n (workflows/credentials).
# Run manually or via the bundled launchd job (scripts/com.aichief.backup.plist).
set -euo pipefail

# P24B-07: a backup is a copy of production secrets. Everything created below -
# directories, the gzipped SQL dump, the n8n database - is owner-only from the
# moment it is created, instead of inheriting the caller's default umask (0644
# files / 0755 directories, world-readable to every local account). umask
# applies at creation time to the `>` redirections and to mkdir; the explicit
# chmod calls afterwards additionally cover the case where an artifact with the
# same timestamp already exists with looser permissions. Pre-existing backups
# are deliberately left exactly as they are.
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="$ROOT/backups"
PG_DIR="$DEST/postgres"
N8N_DIR="$DEST/n8n"
KEEP=14

log() { echo "[backup $(date +%H:%M:%S)] $*"; }

harden_dir() { chmod 700 "$1"; }
harden_file() { chmod 600 "$1"; }

mkdir -p "$PG_DIR" "$N8N_DIR"
harden_dir "$PG_DIR"
harden_dir "$N8N_DIR"

# --- Postgres full dump (gzipped) ---
# Covers everything in the application database, including the Fernet-encrypted
# Google OAuth refresh tokens in user_integrations - which is why there is no
# separate Google token copy below: the legacy backend/credentials/token.pickle
# is no longer written or read at runtime (see backend/migrate_legacy_google_token.py),
# so copying it could only ever produce a stale duplicate of material that is
# already in this dump.
log "dumping postgres..."
if docker compose -f "$ROOT/docker-compose.yml" exec -T postgres \
    pg_dump -U postgres -d ai_chief | gzip > "$PG_DIR/ai_chief_$STAMP.sql.gz"; then
    harden_file "$PG_DIR/ai_chief_$STAMP.sql.gz"
    log "postgres dump: $PG_DIR/ai_chief_$STAMP.sql.gz"
else
    log "WARN: postgres dump FAILED (kept previous backups)"
fi

# --- n8n DB (workflows + encrypted credentials) ---
if docker ps --format '{{.Names}}' | grep -q '^n8n-n8n-1$'; then
    if docker exec n8n-n8n-1 sh -c 'cat /home/node/.n8n/database.sqlite' > "$N8N_DIR/n8n_$STAMP.sqlite"; then
        harden_file "$N8N_DIR/n8n_$STAMP.sqlite"
        log "n8n db: $N8N_DIR/n8n_$STAMP.sqlite"
    else
        log "WARN: n8n dump FAILED"
    fi
else
    log "WARN: n8n container not running, skipped"
fi

# --- retention: keep newest KEEP per subdir ---
# The pattern is deliberately UNQUOTED so the shell expands it; quoting it would
# pass the literal "*.sql.gz" to ls, ls would exit non-zero, and `set -e` would
# abort the run *after* the dumps were written (i.e. retention would never
# happen and every run would look failed). A non-matching pattern is likewise
# not an error: there is simply nothing to prune.
prune() {
    local dir="$1" pattern="$2" listing
    [ -d "$dir" ] || return 0
    # The glob expands to full paths, so each surviving entry is reduced to its
    # basename before being joined back onto $dir.
    listing=$(ls -1t -- "$dir"/$pattern 2>/dev/null || true)
    [ -n "$listing" ] || return 0
    printf '%s\n' "$listing" | tail -n +$((KEEP + 1)) | while IFS= read -r f; do
        rm -f -- "$dir/$(basename "$f")"
    done
}
prune "$PG_DIR" "*.sql.gz"
prune "$N8N_DIR" "*.sqlite"

log "backup complete: $(du -sh "$DEST" 2>/dev/null | cut -f1)"
