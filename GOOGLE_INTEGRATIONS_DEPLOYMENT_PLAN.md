# Google Integrations — Deployment Plan (NOT executed — awaiting approval)

**Prepared:** 2026-10-11 · **Repo HEAD:** `15fd552` · **Live DB migration:** `0009` · **Repo Alembic head:** `0011`

This plan is a **proposal only**. No live migration, container restart, credential change, or
deploy has been performed. Every step below requires explicit approval before it is run.

---

## 1. Current live state (verified read-only 2026-10-11)

| Item | Value |
|---|---|
| Live DB schema | `0009` |
| Migrations present in repo | `0001`…`0011` (head `0011`) |
| Unapplied migrations | `0010` (app_config bootstrap marker), `0011` (users.token_version) |
| Backend container | `aichief-backend-1`, started `2026-10-06T17:28:10Z`, **no `--reload`** |
| Backend source | **bind-mounts** `./backend:/app` (files are shared, but the running process is the 2026-10-06 snapshot) |
| Frontend container | `aichief-frontend-1`, static Next.js build |
| Live users | `1 admin@aichief.local`, `2 member@test.local`, `3 priya@example.com` |
| Live google integrations | user 1 `google`=pending, user 2 `google`=pending, user 3 `google`=revoked; user 1 also has a `gmail`=active row |
| Live google_auth_state | user 1 `not_authorized`, user 3 `not_authorized` |

**Key consequence:** the running backend process started 2026-10-06, before every fix in the
current working tree (all dated 2026-10-11 14:22–14:43). The bind mount means the *files* are
current, but the *process* is running the old in-memory code. **A backend restart is required to
activate the fixes.**

---

## 2. Migration order (reviewed)

```
0009  →  0010  →  0011
```

- **`0010_app_config_bootstrap_marker`** — `CREATE TABLE IF NOT EXISTS app_config`. New table only;
  no existing data touched; idempotent; cannot fail on data.
- **`0011_session_revocation`** — `ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER
  NOT NULL DEFAULT 0`. Additive; existing rows get `0`; idempotent.

Both are additive and idempotent. **Apply `0010` then `0011`, in that order, before restarting the
backend** (the new auth code reads `users.token_version`; without it, token issuance/validation
fails).

---

## 3. Backup & restoration (verified procedure — run before migrating)

```bash
# Backup (run from the repo root)
docker exec aichief-postgres-1 pg_dump -U postgres -d ai_chief \
  --format=custom --file=/tmp/ai_chief_pre_0011.dump
# copy it off the container
docker cp aichief-postgres-1:/tmp/ai_chief_pre_0011.dump ./ai_chief_pre_0011.dump

# Restoration (only if rollback is needed)
docker cp ./ai_chief_pre_0011.dump aichief-postgres-1:/tmp/restore.dump
docker exec aichief-postgres-1 pg_restore -U postgres -d ai_chief \
  --clean --if-exists /tmp/restore.dump
```

Verify the backup file is non-empty and restorable into a throwaway database before trusting it.

---

## 4. Environment & OAuth redirect requirements

| Variable | Required value | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | set | env overrides `credentials.json`; both must be non-empty |
| `OAUTH_REDIRECT_URI` | `http://localhost:8000/api/integrations/google/callback` | **must match the Google Cloud OAuth client exactly** |
| `FRONTEND_URL` | `http://localhost:3000` | where the callback redirects the browser |
| `OAUTH_COOKIE_SECURE` | unset/`true` in prod; `false` only for plain-HTTP local dev | defaults to Secure |
| `CLASSROOM_COURSE_IDS` | comma-separated course IDs, or empty for all | org-specific |

The dev-only `redirect_uri` override in `POST /api/integrations/google/authorize` is restricted to
loopback hosts (`_is_local_redirect`); it cannot be pointed off-machine.

---

## 5. Execution order (once approved)

1. **Backup** the live DB (§3) and verify the dump.
2. **Migrate** the live DB to head:
   ```bash
   cd backend
   ENVIRONMENT=production DATABASE_URL=postgresql+psycopg2://postgres@aichief-postgres-1:5432/ai_chief \
     venv/bin/alembic upgrade head
   ```
   (or `docker exec aichief-backend-1 alembic upgrade head` if the image bundles alembic).
   Confirm `alembic_version` = `0011`.
3. **Rebuild the frontend** image (the production image inlines `NEXT_PUBLIC_API_URL` at build
   time; a runtime env var cannot change an already-built bundle):
   ```bash
   docker compose build frontend
   ```
4. **Restart the backend** so the running process picks up the working-tree fixes (identity-only
   login scopes, access-log redaction, classroom scope validation, reconnect fix):
   ```bash
   docker compose up -d --force-recreate backend
   ```
5. **Restart the frontend** to serve the new build:
   ```bash
   docker compose up -d --force-recreate frontend
   ```
6. **Health checks** (§7).

---

## 6. Rollback

- **Code:** the fixes are uncommitted in the working tree; `git stash` / `git checkout` reverts
  them. The bind mount means a backend restart immediately serves whatever is on disk.
- **DB:** restore the pre-migration dump (§3). Both migrations are additive, so a restore is the
  clean rollback; do **not** rely on `downgrade` in production.
- **Containers:** `docker compose up -d --force-recreate backend frontend` after reverting.

---

## 7. Post-deployment health checks

```bash
# liveness
curl -s http://localhost:8000/api/health        # -> {"status":"ok"}

# confirm the running process has the NEW code (read-only):
#   - the access-log redaction filter is active (no code=/state= in uvicorn access logs)
#   - GET /api/auth/google/login still 307s to Google with the identity scopes only
docker logs --tail 50 aichief-backend-1

# authenticated Google status (use a real session token):
curl -s -H "Authorization: Bearer <token>" http://localhost:8000/api/emails/health
```

Confirm the running process is serving the new code by checking that a request to the OAuth callback
path does **not** log `code=`/`state=` in the access log (the redaction filter is active).

---

## 8. n8n Monitor stale JWT (separate issue)

The n8n workflow `ai-chief-phase2-monitor` uses a static user-JWT credential (`p2-cred-jwt`) →
`401` (170/170 errored executions). This is independent of the Google OAuth fixes.

**Plan (requires n8n admin access; do not paste the JWT into logs or reports):**
1. Open the n8n workflow `AI CHIEF · Phase 2 Monitor`.
2. Open the `p2-cred-jwt` credential and replace the static JWT with a freshly minted one for the
   intended user (same signing key/algorithm the backend accepts).
3. Save the credential and the workflow; activate it.
4. Trigger one manual run and confirm it no longer returns `401`.

The backend already validates the `ver` claim (migration `0011`), so the replacement JWT must carry
the current `token_version` of the target user.

---

## 9. How to confirm the running app is using the deployed changes

- The backend bind-mounts `./backend:/app`, so the *files* are always current; the *process* is not
  (no `--reload`). After `docker compose up -d --force-recreate backend`, confirm:
  - `docker logs` shows a fresh startup, and
  - the access-log redaction is active (no `code=`/`state=` on the OAuth callback path), and
  - `GET /api/auth/google/login` requests only the identity scopes.
- The frontend is a static build; confirm the served bundle hash changed after the rebuild.
