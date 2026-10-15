# Phase F1 — Google Re-authorization & Deployment Readiness Report

**Date:** 2026-10-11 · **App/repo HEAD:** `15fd552` (unchanged) · **Repo Alembic head:** `0011`
**Live DB migration:** `0009` (unchanged) · **Scope:** verification + preparation only (no deploy, no live writes)

This report continues Phase F (`PHASE_F_GOOGLE_VERIFICATION_AND_READINESS_REPORT.md`,
`GOOGLE_INTEGRATION_STATUS.md`). It records what was inspected and tested, what changed in the
working tree, the exact re-authorization step that is waiting on you, and a deployment/rollback
plan. No live database write, migration, container restart, n8n change, or provider mutation was
performed.

---

## 1. Executive summary

- The **application-side** Google integration (OAuth authorize/callback, server-side `state`
  validation, per-user encrypted token storage, refresh, health, disconnect) is wired correctly
  and confirmed by inspection **and** by generating the app's real consent URL and issuing a
  read-only GET to Google: Google replied `302` to its sign-in page for the app's exact
  `client_id` + loopback `redirect_uri` + scope set — i.e. **no `redirect_uri_mismatch`, no
  `invalid_client`**. The earlier concern that the `installed`-type client (registered redirect
  `http://localhost`) would reject the app's path-bearing loopback callback is **not** a blocker.
- The **live provider** side is still **BLOCKED**: the stored refresh token for user 1 was
  re-probed read-only and still fails with `RefreshError`, and the stored scopes still **lack**
  `classroom.coursework.me.readonly`. Re-authorization requires your browser and is the single
  blocking item (§4).
- The **live running backend is not the working tree.** It bind-mounts `./backend:/app` and runs
  `uvicorn` **without `--reload`**, and it started `2026-10-06T17:28:10Z`. So it is a frozen
  snapshot from 2026-10-06; today's Phase F/F1 edits are on disk but **not loaded**. Because the
  working-tree model references `users.token_version` (which does not exist in the `0009` live DB),
  the database **must** be migrated to `0011` **before** the backend is restarted. This is the most
  important deployment ordering fact discovered this phase.
- Two small, safe completeness gaps in the reviewed Phase F work were found and fixed in the
  working tree (see §7): `/api/whatsapp/messages` was the one list endpoint missing the NF-6
  pagination clamp, and `API_MAX_PAGE_SIZE` / `MAX_UPLOAD_MB` were not documented in
  `backend/.env.example`.
- Tests: **446 passed, 0 failed** (full backend suite on a disposable PostgreSQL), focused
  Google/auth/limits run **58 passed**, config/doc-guard run **155 passed**, frontend production
  build **compiled successfully**.
- Live state is unchanged: DB at `0009`, `users=3`, `user_integrations=4`, `google_auth_state=2`;
  all live containers still up; the disposable test DB was removed.
- **Verdict: `BLOCKED`** (§15) — live Gmail/Classroom/Calendar verification cannot proceed until
  the account owner re-authorizes Google (an external browser action). Everything that could be
  done safely without that has been done and documented.

---

## 2. Initial Git and runtime baseline

### 2.1 Repository

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `15fd552ece54a61cd19b0ec63b8e4e9105387149` — "Move all hardcoded business/org data to configurable env settings" |
| Staged changes | 0 |
| Unstaged tracked modifications | 65 |
| Untracked entries | 49 |
| Stashes | none |
| Other authors/agents since Phase F | none observed — same HEAD, no new commits, no stash |

The working tree is the same large, uncommitted body of work seen in Phase F (Phase D/E/F
changes plus new untracked modules such as `backend/app/api/google_oauth.py`,
`backend/app/api/integrations.py`, `backend/app/api/whatsapp.py`, `backend/app/core/limits.py`,
`backend/alembic/versions/`, `backend/tests/`, `frontend/src/app/settings/`,
`frontend/src/app/monitoring/`, `reauthorize_gmail.py`, `scripts/`, `n8n/`). All of it was treated
as user-owned: nothing was reset, cleaned, stashed or deleted. No Git operations were run.

### 2.2 Live runtime

`docker ps` (before **and** after this phase — identical):

| Container | Status | Started |
|---|---|---|
| `aichief-backend-1` | Up | `2026-10-06T17:28:10Z` |
| `aichief-frontend-1` | Up | `2026-10-03T08:54:47Z` |
| `aichief-postgres-1` | Up (healthy) | live DB |
| `aichief-ollama-1` | Up | — |
| `n8n-n8n-1` | Up | — |
| `backend-db-1`, `backend-pgadmin-1` | Up | unrelated stack |

Key deployment fact: `docker-compose.yml` mounts `./backend:/app` (bind, rw) and the backend
command is `uvicorn app.main:app --host 0.0.0.0 --port 8000` **without `--reload`**. Therefore the
running process serves the code as of `2026-10-06`, and a container restart is the point at which
the current working tree (with `users.token_version`) becomes live. Read-only probes run via a
fresh `docker exec … python` **do** import the current working tree (this is how the Phase F NF-3
behaviour was observed).

### 2.3 Live database (read-only)

```
alembic_version  -> 0009          (unchanged; 0010 and 0011 are NOT applied)
users            -> 3
user_integrations-> 4
google_auth_state-> 2
users.token_version column -> ABSENT
```

`user_integrations` (no token material printed):

| id | user | provider | status | account_id | scopes | token_expires_at |
|---|---|---|---|---|---|---|
| 1 | 1 | gmail | active | admin@aichief.local | — | NULL |
| 3 | 2 | google | pending | — | — | NULL |
| 5 | 1 | google | active | — | calendar.events, classroom.courses.readonly, classroom.student-submissions.me.readonly, gmail.readonly | NULL |
| 6 | 3 | google | revoked | — | — | NULL |

`google_auth_state`: user 1 = `healthy` (stale, last checked `2026-10-08`), user 3 =
`not_authorized`. The `healthy` value for user 1 is the **false-health** artifact of the NULL
`token_expires_at` (pre-fix code reads NULL as "valid forever"); the NF-3 fix in the working tree
forces the refresh path instead.

---

## 3. Current OAuth configuration findings (secrets redacted)

- **Client config source:** `backend/credentials/credentials.json`, section **`installed`**
  (Desktop client). It contains a `client_id` and `client_secret` (values not printed), plus
  `redirect_uris: ["http://localhost"]`. The env overrides `GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET` exist but are **empty** (length 0), so the file is authoritative.
- **Configured callback:** `OAUTH_REDIRECT_URI = http://localhost:8000/api/integrations/google/callback`.
- **Frontend origin:** `FRONTEND_URL = http://localhost:3000`.
- **Requested scopes (8, least-privilege):**
  `gmail.readonly`, `classroom.courses.readonly`, `classroom.coursework.me.readonly`,
  `classroom.student-submissions.me.readonly`, `calendar.events`, `openid`,
  `userinfo.email`, `userinfo.profile`.
- **Offline refresh:** the app requests `access_type=offline` + `prompt=consent` (so Google returns
  a refresh token) + `include_granted_scopes=true`.
- **State binding:** `build_authorization_url` mints a `secrets.token_urlsafe(32)` nonce and stores
  it server-side in the user's `user_integrations.meta.oauth_state`; the callback resolves the user
  from that nonce (`_resolve_user_by_state`) and re-checks it with `secrets.compare_digest`. Tokens
  are encrypted at rest with `SECRET_KEY` (Fernet). No token is ever put in a redirect URL.
- **Consent URL validation (read-only GET, client_id redacted):** the app's generated URL was
  fetched once. Google answered `302` → `accounts.google.com/…/signin/identifier?...` carrying the
  app's `redirect_uri`, `scope`, `access_type=offline`, `prompt=consent`,
  `include_granted_scopes=true` and `state`. **No `redirect_uri_mismatch`, no `invalid_client`, no
  `invalid_scope` page.** This confirms the Desktop client accepts the loopback redirect even though
  only `http://localhost` is registered, and that the scope set is accepted for the request.
- **Live process scope confirmation:** the live anonymous `GET /api/auth/google/login` still
  returns a `307` whose redirect requests the same 8 scopes, including
  `classroom.coursework.me.readonly` — i.e. even the running (Oct 6) code requests the missing
  Classroom scope. Re-authorizing through the running app therefore *does* request the scope that
  the stored token currently lacks.

---

## 4. Re-authorization: actions completed and steps awaiting you

**Completed by me (no live write):** traced the full flow; confirmed the client/redirect/scopes are
accepted by Google; confirmed the live app exposes the connect flow (live frontend bundle contains
`/api/integrations/google/authorize` and the `?google=success` handling); confirmed the live
endpoints exist (`/authorize` → `401` unauthenticated, `/callback` → `307` with no params);
confirmed the live credential is still dead via a non-persisted refresh probe.

**Result of the read-only refresh probe (live, non-persisted):**

```
user1_has_creds: True
has_refresh_token: True
expiry_set: True (forced refresh path)
scopes_count: 4
refresh_result: FAIL   (error category: RefreshError)
```

The stored token is still `STORED_CREDENTIALS_DEAD`, exactly as in Phase F. Nothing was written.

### 4.1 What I need from you (single blocking step)

The existing application's own web flow is **not defective**, so use it rather than a second flow:

1. Confirm the app is up: `http://localhost:8000/api/health` → `200` and `http://localhost:3000` →
   `200` (both confirmed at the time of writing).
2. Open **`http://localhost:3000`** and log in as the owner account (**user id 1**). Do not send me
   the password or any token.
3. Go to **Settings** (`http://localhost:3000/settings`).
4. Click **“Connect Google”.**
5. On the Google consent screen, sign in with **the same Google account whose Gmail, Classroom and
   Calendar you want connected** (user 1's account) and **grant every requested permission** —
   in particular the **Classroom “coursework”** permission. Expected scopes:
   `gmail.readonly`, `classroom.courses.readonly`, `classroom.coursework.me.readonly`,
   `classroom.student-submissions.me.readonly`, `calendar.events`, plus `openid`/email/profile.
   - Stripe/warning “Google hasn't verified this app” → it is your own app in **Testing**; choose
     **Advanced → “Go to … (unsafe)”**.
   - “Access blocked: … has not completed the Google verification process” → this Google account is
     not a **Test user**. Add it under Google Cloud Console → *APIs & Services → OAuth consent
     screen → Test users*. (This is a Google Cloud Console change requiring your approval — see
     §14; do not change scopes.)
   - If the consent screen does **not** offer the Classroom coursework scope, the scope must be
     added to the consent screen's scope list (approval-gated configuration change) — tell me and
     stop; do not alter scopes silently.
6. Google redirects back to
   `http://localhost:3000/api/...`→ then to **`http://localhost:3000/settings?google=success`**.
   That page appearing means the callback persisted fresh credentials.
7. Reply “Google re-authorized” (again: no tokens/secrets).

**Existing alternative (not preferred):** the untracked `reauthorize_gmail.py` performs a Desktop
loopback flow and writes an encrypted `backend/credentials/token.pickle`, which
`backend/migrate_legacy_google_token.py` then imports into the owner's `user_integrations` row. It
requests the same minimal scopes (minus `openid`/profile) and would also write to the live DB via
the import step. The in-app web flow is preferred because it persists directly to the correct user
and exercises the production code path.

After you confirm, I will run **read-only, non-mutating** provider checks (§5) and finalize §5/§6/§15.
No provider check will write to the live database; the Gmail **sync** endpoint (which inserts email
rows) will **not** be executed without your explicit approval.

---

## 5. Per-service verification results

Status vocabulary: `PASS` / `FAIL` / `BLOCKED` / `NOT TESTED` / `MOCK·UNIT TESTED`.

### 5.1 Gmail
| Check | Result | Evidence |
|---|---|---|
| OAuth authorize/callback code path | MOCK·UNIT TESTED | `tests/test_p24_security.py`, `tests/test_p24c_security.py`, `tests/test_integrations.py` |
| Per-user credential storage / isolation | MOCK·UNIT TESTED | `tests/test_per_user_sync.py`, `test_gmail_cache_isolation.py`, `tests/test_isolation.py` |
| Refresh-error/re-auth handling | MOCK·UNIT TESTED | `tests/test_refresh_error_handling.py` |
| Consent URL + scopes accepted by Google | PASS | read-only `302` to sign-in (§3) |
| Token refresh of the **stored live** credential | **BLOCKED** | `RefreshError` on a non-persisted read-only refresh (§4) |
| Live Gmail read (`users.messages.list`) | BLOCKED | requires re-authorization |
| Live sync → DB rows | NOT TESTED (by policy) | would write to the live DB; withheld pending approval |

### 5.2 Google Classroom
| Check | Result | Evidence |
|---|---|---|
| List/coursework service + error handling | MOCK·UNIT TESTED | `tests/test_per_user_sync.py`, `tests/test_refresh_error_handling.py` |
| `classroom.coursework.me.readonly` requested by the flow | PASS | live `GET /api/auth/google/login` redirect includes it (§3) |
| Stored token contains coursework scope | **FAIL** | stored scopes lack `classroom.coursework.me.readonly` (row 5) |
| Live course/coursework read | BLOCKED | dead credential; re-auth pending |

Pagination / error handling: `classroom_service` read paths and the app-level NF-6 clamps are
unit-tested; live pagination over Google's API cannot be exercised until re-auth (`BLOCKED`).

### 5.3 Google Calendar
| Check | Result | Evidence |
|---|---|---|
| Authorize/refresh/scope handling | MOCK·UNIT TESTED | `tests/test_integrations.py`, `tests/test_refresh_error_handling.py` |
| Read-only calendar operation | BLOCKED | dead credential; re-auth pending |
| Write-back (event create) | NOT TESTED | out of scope; **not** inferred from any read result |

### 5.4 Mock vs real evidence ledger
- **Real provider contact this phase:** exactly one read-only token-refresh probe and one read-only
  HTTP GET of the app's consent URL to `accounts.google.com` (no login, no consent submitted). No
  message, event, coursework or token content was read or logged.
- **Everything else is code inspection or mocked/unit tests.** No live Gmail/Classroom/Calendar
  data was retrieved because the credential is dead.

---

## 6. Exact operations performed against real services

- **Google token endpoint:** one in-memory `credentials.refresh()` probe for user 1
  (non-persisted). Result: `RefreshError` (credential dead). No request/response body logged.
- **`accounts.google.com`:** one read-only `GET` of the app-generated authorization URL (no
  credentials submitted, no consent granted). Result: `302` to sign-in — client/redirect/scope
  accepted. The redirect token/`client_id` was redacted.
- **n8n:** read-only inspection of a **copy** of the SQLite DB (including `-wal`/`-shm`), no writes,
  no workflow runs, no credential decryption.
- **Live Postgres:** `SELECT`/schema introspection only. No writes, no migrations.

Nothing else touched Google. No WhatsApp send, no Calendar/Classroom mutation, no email send.
No email bodies, event details, coursework, tokens or client secrets were printed or logged.

---

## 7. Code changes made (file paths and rationale)

Phase F1 made three small, safe changes on top of the reviewed Phase F work; the Phase F changes
themselves remain in place and were reviewed again.

**Phase F1 changes:**

| File | Change | Rationale |
|---|---|---|
| `backend/app/api/whatsapp.py` | `from app.core.limits import clamp_limit`; `limit = clamp_limit(limit)` in `GET /api/whatsapp/messages` | This was the **one** list endpoint that Phase F's NF-6 sweep missed (the Phase F report's NF-6 row lists emails/tasks/events/documents/notifications/news only). Confirmed defect, same reviewed control, no external dependency. Nothing calls this endpoint from the frontend, so UI impact is nil. |
| `backend/tests/test_phase_f_security.py` | Added `/api/whatsapp/messages` to the extreme-pagination parametrization; added `test_whatsapp_messages_clamps_limit` (spies `clamp_limit` to prove the handler routes the caller's limit through it) | Regression coverage for the fix; asserts success and boundary. |
| `backend/.env.example` | Documented `API_MAX_PAGE_SIZE=200` and `MAX_UPLOAD_MB=25` | Phase F added these settings with safe defaults but did not document them; the review item "env vars have safe defaults and are documented" was unmet. Documentation-only. |

**Phase F changes re-reviewed (all present and correct):**

| File | Item | Note |
|---|---|---|
| `backend/app/models/user.py` | NF-1 `token_version` column | present |
| `backend/alembic/versions/0011_session_revocation.py` | NF-1 migration | `ADD COLUMN IF NOT EXISTS … DEFAULT 0`; additive/idempotent |
| `backend/alembic/versions/0010_app_config_bootstrap_marker.py` | prerequisite migration | `CREATE TABLE IF NOT EXISTS app_config`; additive/idempotent |
| `backend/app/core/security.py` | `create_token(ver=…)` | `ver` always written (default 0) |
| `backend/app/api/deps.py` | `token_is_current` enforced in `get_current_user` | missing `ver` treated as 0, so legacy tokens survive |
| `backend/app/api/auth.py` | `POST /api/auth/logout`, `/status`, register path | bump + checks present |
| `backend/app/services/google_auth.py` | NF-3 false-health fix | NULL expiry → epoch, refresh path exercised |
| `backend/app/core/limits.py` + list endpoints | NF-6 clamps | now complete (whatsapp added) |
| `backend/app/api/documents.py` | upload cap → `413` | reads `max_bytes + 1` before buffering |
| `backend/app/core/config.py` | `API_MAX_PAGE_SIZE=200`, `MAX_UPLOAD_MB=25` | safe defaults |
| `frontend/src/lib/api.ts`, `frontend/src/app/settings/page.tsx` | `api.auth.logout()` + "Sign out" | revokes server-side, then clears local token |

Legacy-token behaviour is verified by
`test_legacy_token_without_ver_claim_still_valid_at_epoch_zero`. Downgrading `0011` drops the
column; because the ORM model references it, a downgrade is only safe together with reverting the
code (see §13 rollback).

---

## 8. Focused and full test results

All runs used a **newly created disposable PostgreSQL 16** container (host port `22580`), completely
separate from the live stack and from the existing `ai_chief_test`. The container was removed at the
end. Environment: `ENVIRONMENT=testing` plus the disposable `DATABASE_URL`; `SECRET_KEY`,
`INGEST_API_KEY` and `AUTH_PASSWORD` were **unset** (an empty `AUTH_PASSWORD` in the environment is
what caused two false baseline failures in Phase F, so it is explicitly unset).

| Run | Command scope | Result |
|---|---|---|
| Focused Google/auth/limits | `test_phase_f_security.py`, `test_auth.py`, `test_phase_e_security.py` with `-k "google or oauth or auth or logout or session or token or limit or upload or document or health"` | **58 passed** |
| Phase F security file (after the whatsapp fix) | `test_phase_f_security.py` | **21 passed** |
| Full backend suite | all `tests/test_*.py` | **446 passed, 0 failed** (2 min 0 s) |
| Config / docs-guard / compose hardening | `test_config_hardening.py`, `test_phase_d_compose_hardening.py` | **155 passed** |

No test was disabled, skipped, or weakened. The known flaky pre-existing test from Phase F
(`tests/test_global_uniqueness.py::test_push_delivery_follows_rebound_endpoint`, timing/order
dependent, unrelated to Phase F/F1) passed in both full runs this phase; it remains disclosed as a
latent flake.

---

## 9. Frontend build and secret-scanning results

- **Frontend production build:** `npm run build` in `frontend/` compiled successfully, lint and
  type-check passed, all 18 routes prerendered (including `/settings` and `/monitoring`).
- **Secret scanning:** the repository doc guard
  `test_repository_docs_contain_no_working_credentials` (no inline DSN passwords, no JSON password
  examples in any `*.md`) passes, and the WhatsApp-webhook / compose-hardening secret checks pass.
  This report deliberately contains no inline connection string, token, client id or secret.

---

## 10. Live database migration version

`alembic_version = 0009` **before and after** this phase — **unchanged**. `0010` and `0011` are not
applied; `users.token_version` is absent. All testing used the disposable database and did not touch
the live DB.

---

## 11. Live container/service status before and after

Identical `docker ps` before and after (§2.2): backend, frontend, postgres, ollama and n8n all still
up, no restarts. `http://localhost:8000/api/health` → `200`; `http://localhost:3000/` → `200` after
the work. The disposable `phasef-pg` container was created and then removed; it never joined the
`aichief` network.

---

## 12. n8n findings and proposed correction

Read-only inspection of a copy of the n8n SQLite database:

- **Workflows:** `AI CHIEF · Phase 2 Monitor` (active), `AI CHIEF · WhatsApp Inbound` (active),
  `AI CHIEF Phase 1 POC` (inactive).
- **Credentials:** `AI Chief Backend JWT` (type `httpBearerAuth`) and `AI Chief Ingest API`
  (type `httpHeaderAuth`). Names/types only — the encrypted values were **not** decrypted.
- **Monitor (`AI CHIEF · Phase 2 Monitor`)** calls the backend with:
  - `GET {AI_CHIEF_BACKEND_URL}/api/emails?limit=20`
  - `GET {AI_CHIEF_BACKEND_URL}/api/emails?limit=50&category=important`
  - `POST {AI_CHIEF_BACKEND_URL}/api/whatsapp/send`
  using the **static bearer credential `AI Chief Backend JWT`** (`authentication:
  predefinedCredentialType`). That credential is a hand-minted **user JWT**, and user tokens expire
  after `AUTH_TOKEN_TTL_DAYS` (default 7). This is the exact root cause of the failures.
- **Executions:** `170` total for the Monitor — **170 `error`, 0 `success`** (the Phase E/F figure of
  166 has since grown). `AI CHIEF · WhatsApp Inbound` and the Phase 1 POC workflow have no recorded
  executions in this DB.

**Proposed correction (needs your approval; no live change made):**
1. **Preferred — server-to-server key.** Extend the existing ingest-key-guarded family
   (`/api/integrations/agent/*`, verified with `X-API-Key` via `INGEST_API_KEY`) with a read-only
   “list emails for an account” endpoint, then have the Monitor use the already-present
   `AI Chief Ingest API` header credential. `INGEST_API_KEY` does not expire, so the workflow stops
   breaking every 7 days and no user password/JWT is stored. (Requires a small code change +
   review.)
2. **Fallback — login-per-run.** Keep the bearer credential but replace the static JWT with a
   pre-request to `/api/auth/login` using a dedicated application password stored in an n8n
   credential, then use the short-lived returned token. Still stores a user credential and needs
   refresh-on-401 handling.

Do **not** paste a permanent user JWT into the workflow or source in the meantime. I will not edit
the live workflow without explicit approval.

---

## 13. Deployment and rollback plan

No step below was executed.

**Preconditions**
1. Back up the live DB and verify the backup is readable. `scripts/backup.sh` and its launchd plist
   exist but were **not** modified or run. (I cannot claim a fresh backup exists until you run/verify
   one.)
2. Freeze writes / accept a brief maintenance window (login and writes are briefly unavailable
   during the restart).

**Order (critical)**
1. **Back up** the live database.
2. **Migrate first, while the old process is still up:** `alembic upgrade head` (applies `0010`
   then `0011`). Both are purely additive/idempotent and backward compatible:
   - `0010` → `CREATE TABLE IF NOT EXISTS app_config` (new table only).
   - `0011` → `ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0`
     (existing rows get `0`; pre-existing tokens without a `ver` claim are treated as 0, so nothing
     is invalidated by applying it).
   The old process keeps running unchanged because its model does not select the new column.
2. **Restart the backend container** so the working-tree code loads. This must happen **after** step
   2: the new ORM model selects `users.token_version`, so restarting against a `0009` DB would raise
   `column users.token_version does not exist` on every authenticated query.
3. Verify: `/api/health` 200; log in; `GET /api/auth/status`; `POST /api/auth/logout` then confirm the
   old token is rejected (`/api/auth/me` → 401) and a fresh login works.
2. Rebuild + restart the frontend image so the “Sign out” button ships (the current frontend
   container is the `2026-10-03` build).
3. Google health: `GET /api/emails/health` should now report the true state.
4. n8n: apply the §12 correction separately (own approval, own verification).

**Rollback**
- The two migrations are additive, so the **database backup** is the ultimate rollback.
- `alembic downgrade 0009` drops `users.token_version` and the `app_config` table. This must be done
  **in lock-step with reverting the code**: once the `0011`-era code is running, dropping
  `token_version` makes the ORM fail exactly as above, and dropping `app_config` re-opens first-owner
  bootstrap on an empty `users` table. Prefer: revert code → restart → (only if required) downgrade.
- No schema change is data-destructive: the column is additive and defaults to 0, so a rollback
  loses only the per-user epoch counter (all tokens simply become epoch-0 again).

**Environment variables:** no new *required* variable. `API_MAX_PAGE_SIZE` (200) and
`MAX_UPLOAD_MB` (25) have safe in-code defaults and are now documented in `backend/.env.example`.
The production secret policy is unchanged.

---

## 14. Remaining blockers, risks and next actions

**Blocking**
1. **Google re-authorization (external).** The stored live credential is dead and the Classroom
   coursework scope is missing. This is the single item preventing live Gmail/Classroom/Calendar
   verification and use. Follow §4. **I cannot complete this for you and will not ask for tokens.**

**Risks**
2. **Deploy ordering.** The backend bind-mounts the repo and runs the Oct 6 snapshot with no
   `--reload`. The DB **must** be at `0011` before the backend restarts, or login/auth will 500.
3. **Google consent screen.** The app is a Desktop client likely in *Testing* mode: the Google
   account must be a Test user, or consent is blocked; the Classroom **coursework** scope must be
   offered by the consent screen, or it will be withheld (a Google Cloud scopes change needs your
   approval and is not a code change).
3. **Signature/verification warning** for an unverified app is expected for your own app in Testing.
4. **n8n Monitor** remains 170/170 failed until its credential is corrected (NF-7).
4. **Latent flake** `tests/test_global_uniqueness.py::test_push_delivery_follows_rebound_endpoint`
   (order/timing dependent; passed this phase).

**Open security items carried from Phase F (documented, not changed):** NF-2 process-local login
throttling; NF-4 WhatsApp owner-fallback; NF-5 register 409 enumeration + login timing; NF-8 401
outside CORS (info); NF-9 `/docs`/`/openapi.json` session-gated (info); NF-10 middleware
`is_authenticated` ignores account status on `/api/news`.

**Next actions**
1. You complete §4 (Google re-authorization) and tell me.
2. I run read-only Gmail `messages.list`, Classroom `courses.list`/`courseWork.list`, and Calendar
   read-only checks through the app's own credential/service path (no DB writes), then update §5/§6
   and the verdict.
3. You decide on deploying (§13) and on the n8n correction (§12).

---

## 15. Final verdict

**`BLOCKED`** — the OAuth flow, scopes, state handling, token storage and the reviewed Phase F
security fixes are implemented and verified by inspection and **446 passing tests** (focused 58,
config/docs 155, frontend build clean), and the app's real consent URL is accepted by Google
(`302`, no `redirect_uri_mismatch`). But **live provider verification of Gmail, Classroom and
Calendar cannot be performed** because the stored Google refresh token is dead and re-authorizing it
is an external browser action that is still pending on you (§4). The live database is unchanged at
migration `0009`, and because the backend bind-mounts the working tree, migration `0010`+`0011`
must be applied **before** the backend is restarted (§13). Complete §4 to unblock; the remaining
items are the deploy approval and the n8n authentication correction.

Required closing line: **BLOCKED** — awaiting Google re-authorization (external) and deploy approval.
