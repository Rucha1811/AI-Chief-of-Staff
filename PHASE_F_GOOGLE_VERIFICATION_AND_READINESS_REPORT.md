# Phase F — Google Integration End-to-End Verification & Readiness Report

**Date:** 2026-10-11
**Branch:** `main` · **HEAD:** `15fd552ece54a61cd19b0ec63b8e4e9105387149`
**Scope:** Verify Gmail, Google Classroom and Google Calendar end to end; close the confirmed
security gaps that were in scope; do **not** deploy, do **not** touch the live database, do
**not** perform external/provider actions without approval.

> **Read this first.** The single most important result: **the Google account stored in the
> live database can no longer be used.** A read-only refresh of the stored refresh token
> against Google's token endpoint fails (`RefreshError`). Until the account is re-authorized
> (an external credential action, which this phase is not permitted to perform), no live
> Gmail / Classroom / Calendar read can succeed. All three services are therefore **BLOCKED**
> for *real-provider* verification. What *is* verified is the application-side flow,
> authorization, state handling, error/re-auth paths and pagination/upload safety — via the
> full offline test suite. This report keeps those two kinds of evidence strictly separate.

---

## 1. Executive summary

| Question | Answer |
|---|---|
| Is a Google OAuth client configured in the running stack? | **Yes** — falls back to `backend/credentials/credentials.json` (`installed` section) because `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` env vars are *present but empty*. |
| Does the sign-in entry point work? | **Yes** — anonymous `GET /api/auth/google/login` issues a `307` to `accounts.google.com` with the correct scopes and redirect URI. |
| Is the stored live Google credential usable? | **No** — the refresh token is dead (`RefreshError` on refresh). **This is the blocker.** |
| Does the app currently know the credential is dead? | **No (before this phase)** — a `NULL` expiry made `google.auth` treat the stored access token as "valid forever", so health reported **healthy without ever contacting Google**. Fixed in this phase (NF‑3). |
| Gmail end-to-end (real provider) | **BLOCKED** — dead credential. |
| Classroom end-to-end (real provider) | **BLOCKED** — dead credential, *plus* the stored token is missing the coursework scope. |
| Calendar end-to-end (real provider) | **BLOCKED** — dead credential. |
| Application-side flow / authz / state / error & re-auth paths | **VERIFIED** by unit + API tests (mock-based). |
| Security fixes delivered in-repo this phase | Session revocation (logout), false-health fix, bounded pagination, upload cap, frontend sign-out. **Not deployed.** |

**Overall verdict: BLOCKED for real-provider verification.** The application code path is
intact and tested; the live Google credential is dead and re-authorizing it requires the
account owner to complete a consent flow (outside this phase's allowed actions). See
§13 for the exact approvals needed.

---

## 2. Baseline — Git and live state

### 2.1 Repository

- Branch `main`, HEAD `15fd552ece54a61cd19b0ec63b8e4e9105387149`.
- Working tree at the start of Phase F: **0 staged**, **65 tracked-modified**, **46 untracked
  directory entries**, carried over from earlier phases; Phase F adds new files (see §11).
- Alembic **repository head is now `0011`** (added this phase). Before this phase the repo
  head was `0010`.

**Test command (single reachable Alembic head):**

```
cd backend
./venv/bin/alembic heads
# -> 0011 (head)
```

**Live runtime state (read-only):**

| Check | Result |
|---|---|
| `aichief-backend-1` | Up, `127.0.0.1:8000->8000/tcp` |
| `aichief-frontend-1` | Up, `127.0.0.1:3000` |
| `aichief-ollama-1` | Up, `127.0.0.1:11435` |
| `aichief-postgres-1` | Up (healthy), `127.0.0.1:5434` |
| `n8n-n8n-1` | Up, `127.0.0.1:5678` |
| Live DB `ai_chief` Alembic revision | **`0009`** (unchanged — no migration was applied) |
| Live `users` / `user_integrations` / `google_auth_state` rows | **3 / 4 / 2** (unchanged) |
| Repo head | `0011` (new) |

> Unrelated to this project but visible on the host: `backend-db-1` (Postgres) and
> `backend-pgadmin-1` bind `0.0.0.0` (LAN-exposed). Informational only; out of scope.

---

## 3. OAuth flow and scope analysis (application-side)

### 3.1 Where the client config comes from

`google_auth._client_config()` prefers `settings.GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`,
and only if those are missing falls back to `backend/credentials/credentials.json`. In the
live backend the two env vars are **present but empty**, so the file is used:

- Section: `installed`
- `redirect_uris: ["http://localhost"]`
- `client_id`: `273071891094-…` (redacted)
- `client_secret`: present (redacted)

The app builds its consent URL with `redirect_uri = settings.OAUTH_REDIRECT_URI`
= `http://localhost:8000/api/integrations/google/callback`, not the JSON's `http://localhost`.

### 3.2 Scopes requested (least privilege)

`app/services/google_auth.py::SCOPES`:

| Service | Scope | Access |
|---|---|---|
| Gmail | `gmail.readonly` | read |
| Classroom | `classroom.courses.readonly` | read |
| Classroom | `classroom.coursework.me.readonly` | read |
| Classroom | `classroom.student-submissions.me.readonly` | read |
| Calendar | `calendar.events` | read + create events (no settings/delete) |
| Identity | `openid`, `userinfo.email`, `userinfo.profile` | identity binding |

Consent URL is built with `access_type="offline"`, `prompt="consent"`,
`include_granted_scopes="true"`. `_SCOPE_SUPERSETS` lets a legacy broad `calendar` grant
satisfy the narrower `calendar.events`/`calendar.readonly` requests.

### 3.3 The two OAuth flows

**(a) Account connection flow (the one that matters for the three services)**

1. Frontend Settings → "Connect Google" → `GET /api/integrations/google/authorize`
   (**session required**). An optional caller `redirect_uri` is validated by
   `_is_local_redirect()`: parsed hostname must be exactly `localhost` / `127.0.0.1` / `::1`,
   scheme `http(s)`, and **no** embedded userinfo. A prefix check is deliberately *not* used.
2. `build_authorization_url()` creates a `state` nonce (`secrets.token_urlsafe(32)`) and
   stores it **server-side** in the user's `user_integrations.meta["oauth_state"]` (row moved
   to `status="pending"` if new).
2. Browser → Google consent → `GET /api/integrations/google/callback?code&state`.
   This route is classified `CREDENTIAL_PUBLIC` (it authenticates via its own state, not a
   bearer token).
3. `_resolve_user_by_state` finds the user by matching the stored nonce; then
   `exchange_authorization_code` verifies the nonce with `secrets.compare_digest`, exchanges
   the code, fetches identity (`id_token` verify, else userinfo), and persists encrypted
   tokens + metadata, then records `AuthStateStore.renew_expiry`.
4. Any error path redirects to `FRONTEND_URL/settings?google=error&reason=…` without ever
   leaking provider error text into the URL.

A `state` beginning with `login_` is handled by `app/api/auth.py::handle_google_login_callback`
(the separate *sign-in* flow, which validates an `oauth_state` **cookie** and promotes/creates
a user). Sign-in and connect are deliberately separate flows.

### 3.4 Token storage, refresh, health and disconnect

- **Storage:** `user_integrations.refresh_token_encrypted` / `access_token_encrypted`
   (Fernet with `SECRET_KEY`), `token_expires_at`, `scopes`.
- **Load:** `credentials_for_user` returns credentials only when the row is `status="active"`
   **and** has a decrypted refresh token.
- **Refresh:** `ensure_refreshed_for_user` refreshes when `not creds.valid`, converts
   `RefreshError`/timeout into a `GoogleAuthError`, and marks the auth state dead.
- **Health:** `probe_credentials_for_user` loads credentials, forces a refresh, and records
   `AuthStateStore` state; `AuthStateStore.get_state()` reports `connected = (state == "healthy")`.
4. **Disconnect:** `POST /api/integrations/google/disconnect` sets `status="revoked"`; tokens
   are retained but `credentials_for_user` then returns `None`, forcing re-auth.

### 3.5 Live observations (read-only)

```
GET /api/auth/google/login            -> 307 to accounts.google.com (client_id 273071891094-…,
                                         redirect_uri http://localhost:8000/api/integrations/google/callback,
                                         scopes gmail.readonly, classroom.courses.readonly,
                                         classroom.coursework.me.readonly,
                                         classroom.student-submissions.me.readonly, calendar.events,
                                         openid, userinfo.email, userinfo.profile)
GET /api/integrations/google/callback -> 307 to frontend /settings?google=error&reason=missing_params
GET /api/webhooks/whatsapp?..wrong..  -> 403
POST /api/auth/logout (anonymous)     -> 401   (live build predates this phase; the middleware
                                                 rejects unknown /api paths without a bearer token)
```

---

## 4. Live provider verification result — **BLOCKED**

A **read-only, non-persisted** refresh of the stored refresh token for user 1 was attempted
inside the live backend container against Google's real token endpoint:

- First attempt with the container's (empty) env client id → Google rejected:
  `invalid_request: Could not determine client ID from request`.
- Corrected attempt using the app's own `_client_config()` (i.e. the `credentials.json`
  client) → **`RefreshError`** from Google.

**Conclusion: `STORED_CREDENTIALS_DEAD`.** The stored refresh token is no longer accepted by
Google. No live read of Gmail, Classroom or Calendar could be performed.

### 4.1 The false-health defect that hid this

Live `user_integrations` row 5 (user 1, provider `google`, `status=active`) has
`token_expires_at = NULL` and a non-null access token. `google.auth` treats
`expiry is None` as *valid*, so `Credentials.valid` returned `True` and
`ensure_refreshed_for_user` **returned early without contacting Google**. The UI therefore
showed Google as connected/healthy while the credential was dead.

Live state captured:

| id | user | provider | status | scopes | token_expires_at | account_id |
|---|---|---|---|---|---|---|
| 1 | 1 | gmail | active | — | NULL | admin@aichief.local |
| 3 | 2 | google | pending | — | NULL | — |
| 5 | 1 | google | active | `calendar.events` `classroom.courses.readonly` `classroom.student-submissions.me.readonly` `gmail.readonly` | **NULL** | — (none) |
| 6 | 3 | google | revoked | — | NULL | — |

`google_auth_state`: user 1 = `healthy` (last checked 2026-10-08), user 3 = `not_authorized`.

**Two defects visible here:**
1. **NF‑3 (fixed this phase):** `NULL` expiry ⇒ false "healthy" without a provider call.
2. **Scope gap (new):** row 5 does **not** include
   `classroom.coursework.me.readonly`, even though the app requests it. A Classroom
   coursework read would fail on scope even if the credential were alive.

---

## 5. Per-service verification matrices

Legend: **✔ verified** (offline/mock-based, deterministic) · **◑ partial** · **∅ not verifiable
against the real provider** · **✘ broken**.

### 5.1 Gmail

| Layer | Path | Real provider | Offline |
|---|---|---|---|
| Frontend | `frontend/src/app/emails/*`, `lib/api.ts` (`api.emails.*`) | — | ✔ (build + existing tests) |
| Authz route | `GET /api/emails`, `POST /api/emails/sync`, `GET /api/emails/health` (`get_current_user`) | — | ✔ (401 anon; scope-isolated) |
| Consent/callback | `GET /api/integrations/google/authorize` → `…/callback` | ◑ (entry point issues `307`; code exchange **∅**) | ✔ (state validation, error redirects) |
| State validation | `secrets.compare_digest`, server-side nonce | ∅ | ✔ (`test_p24c_security`) |
| Token storage | `refresh_token_encrypted` / `access_token_encrypted` (Fernet) | ∅ | ✔ (isolation tests) |
| Refresh | `ensure_refreshed_for_user` | ∅ (**refresh fails**) | ✔ (`test_refresh_error_handling`) |
| Provider API | `gmail_service.sync_emails`, `fetch_recent_emails` → `build_service("gmail","v1")` | ∅ | ✔ (mocked user service) |
| Processing/persistence | `analyze_email_in_db`, `Email` rows by `user_id` | ∅ | ✔ (`test_per_user_sync`, `test_isolation`) |
| Display | `GET /api/emails` list | ∅ | ✔ |
| Error/re-auth | `GmailAuthError → 503`, `GmailUnavailableError → 502` | ∅ | ✔ (`test_refresh_error_handling`) |

**Gmail verdict: BLOCKED** (real provider) · application path **✔ VERIFIED (mock)**.

### 5.2 Classroom

| Layer | Path | Real provider | Offline |
|---|---|---|---|
| Frontend | `assignments`, `exams` pages, `api.classroom.*` | — | ✔ |
| Authz route | `GET /api/classroom/status\|courses\|assignments`, `POST /api/classroom/sync\|sync-submissions` | — | ✔ |
| Scope | `courses.readonly`, `coursework.me.readonly`, `student-submissions.me.readonly` | **∅ — stored token missing `coursework.me.readonly`** | — |
| Provider API | `classroom_service.list_courses/list_assignments/list_submissions` | ∅ | ✔ (mocked) |
| Refresh/error | `ClassroomAuthError → 503` | ∅ | ✔ (`test_refresh_error_handling`) |
| Persistence | assignments → tasks/exams by `user_id` | ∅ | ✔ (`test_per_user_sync`) |

**Classroom verdict: BLOCKED** (real provider) · application path **✔ VERIFIED (mock)** ·
**plus a confirmed scope gap** in the stored credential.

### 5.3 Calendar

| Layer | Path | Real provider | Offline |
|---|---|---|---|
| Frontend | `calendar` page, `api.events.*` | — | ✔ |
| Authz route | `POST /api/events/sync-to-google`; event CRUD under `user_id` | — | ✔ |
| Scope | `calendar.events` (supersets `calendar`/`calendar.readonly` accepted) | ∅ | ✔ (`has_scope`) |
| Provider API | `calendar_service.push_events_to_calendar` | ∅ | ✔ |
| Refresh/error | `CalendarAuthError` | ∅ | ✔ |
| Persistence | `events` user-scoped; conflicts/free-slots | ∅ | ✔ (`test_teams_isolation`) |

**Calendar verdict: BLOCKED** (real provider) · application path **✔ VERIFIED (mock)**.

---

## 6. Mock vs real evidence ledger

| Claim | Evidence kind | Where |
|---|---|---|
| OAuth entry point issues a correct consent redirect | **Real** (live `307`) | §4 curl |
| OAuth callback rejects missing/garbage state | **Real + offline** | live `307 missing_params`; `test_p24c_security` |
| Dead credential surfaces as auth error, not 500 | **Offline** | `test_refresh_error_handling` (503) |
| Per-user isolation of synced data | **Offline** | `test_per_user_sync`, `test_isolation`, `test_gmail_cache_isolation` |
| Stored credential is dead | **Real** (read-only refresh) | §4 |
| Gmail/Classroom/Calendar provider reads | **∅ none** | — |
| False-health on `NULL` expiry | **Real (observed) + Offline (now fixed)** | §4.1, `test_missing_expiry_is_not_treated_as_valid` |

No live provider read is claimed anywhere in this report.

---

## 7. Security findings and fixes

### Implemented this phase (in-repo, **not deployed**)

| ID | Finding | Fix | Regression test |
|---|---|---|---|
| **NF‑1** | No way to revoke a session; a stolen token stayed valid for its full TTL, and the frontend had no sign-out. | `users.token_version`; tokens carry a `ver` claim; `get_current_user` rejects a stale `ver`; `POST /api/auth/logout` increments it; migration `0011`; frontend “Sign out”. | `test_phase_f_security.py` (9 tests) |
| **NF‑3** | A `NULL` token expiry made Google health report “healthy” without contacting Google. | `_creds_from_row` materialises a missing expiry as an epoch, forcing the refresh path. | 2 tests |
| **NF‑6** | Unbounded `limit` on list endpoints; document upload read the whole body with no size cap. | `settings.API_MAX_PAGE_SIZE=200` + `clamp_limit`/`clamp_offset` applied to tasks/events/documents/emails/notifications/news; `settings.MAX_UPLOAD_MB=25` → uploads over the cap return `413` before buffering. | clamp unit tests, extreme-pagination API test, 413/accept upload tests |
| Doc | The Phase E report contained inline DSN examples that tripped the repo’s “docs contain no working credentials” guard. | Examples rewritten without inline credentials. | `test_repository_docs_contain_no_working_credentials` |

### Documented, intentionally **not** changed

| ID | Finding | Why not changed |
|---|---|---|
| **NF‑2** | Login brute-force throttle is **process-local** (`defaultdict`), so it is not shared across workers/restarts. | A correct fix needs shared state (Redis/DB); owner decision. |
| **NF‑4** | WhatsApp inbound with an unrecognised sender is attributed to the **owner** (`owner_user_id` fallback). | Changes message ownership semantics; needs product decision. |
| **NF‑5** | `/api/auth/register` returns `409` for an existing email (enumeration) and login timing can leak existence; throttling is per-IP, not per-account. | Behaviour change with product/UX impact. |
| **NF‑7** | Live n8n “Monitor” workflow still calls the backend with a **stale JWT** → `401`; 166 executions, all errors. | Fixing requires updating live n8n credentials (external action). |
| **NF‑8** | `401` responses to credentialed requests bypass the CORS layer (INFO). | No security impact observed. |
| **NF‑9** | `/docs`, `/redoc`, `/openapi.json` are session-gated (INFO). | Correct hardening; documented only. |
| **NF‑10** | The middleware’s `is_authenticated` is signature-only and ignores account status; the only authenticated route that relies on it is `/api/news`. | Low impact; `/api/news` is public content. A revoked token can still fetch news. |

---

## 8. WhatsApp webhook & attribution

- **Provider:** live `WHATSAPP_PROVIDER=none` → the webhook is inert in production today.
- **Signature verification (verified by code + tests):**
  - Twilio: HMAC-SHA1 over URL + sorted params, constant-time compare.
  - Meta: `X-Hub-Signature-256` = HMAC-SHA256 of the **raw body**, constant-time compare.
  - `GET` verification requires the configured verify token; live wrong-token → `403`.
- **Public but gated:** `/api/webhooks/whatsapp` is in the public path set, but the handler
  enforces the provider signature before persisting anything.
- **Attribution (NF‑4):** inbound messages from an unknown sender fall back to the owner.
  Documented above; not changed.

---

## 9. n8n inventory & auth

Read-only inspection of the n8n SQLite database (copied together with `-wal`/`-shm`):

| Workflow | State | Updated |
|---|---|---|
| `AI CHIEF Phase 1 POC` | inactive | — |
| `AI CHIEF · Phase 2 Monitor` | **active** | 2026-09-12 |
| `AI CHIEF · WhatsApp Inbound` | **active** | 2026-09-13 |

Credentials: `AI Chief Backend JWT` (`httpBearerAuth`, 2026-09-12) and
`AI Chief Ingest API` (`httpHeaderAuth`, 2026-09-04).

**Live failure:** node “1 · Sync Emails (Gmail)” → `HTTP 401 Authentication required` /
`Authorization failed - please check your credentials`. 166 executions, **all** `error`,
latest 2026-10-11 03:45. **NF‑7**: the stored n8n JWT is stale. Correcting it is an external
credential change (not performed).

---

## 10. Regression test execution

Test database isolation: a **disposable** PostgreSQL 16 container (`phasef-pg`,
`127.0.0.1:22580`, throwaway password) was used throughout. The harness recreates its own
`ai_chief_test` **inside that container**. The live `ai_chief_test` was never used or modified.

```
cd backend
export ENVIRONMENT=testing
export DATABASE_URL="postgresql+psycopg2://postgres@127.0.0.1:<port>/postgres"
# password supplied out-of-band to libpq (PGPASSWORD), throwaway, never recorded

./venv/bin/python -m pytest tests/test_phase_f_security.py -q -p no:cacheprovider
# -> 19 passed

./venv/bin/python -m pytest -q -p no:cacheprovider
# -> 444 passed
```

- Phase E baseline was **425 passed**; Phase F adds **19** tests and all pass, **0 failures**.
- Frontend: `npm run build` in `frontend/` → **compiled successfully** (type-check clean,
  18 static pages generated).
- **Flaky test (pre-existing, disclosed):** one full-suite run reported
  `tests/test_global_uniqueness.py::test_push_delivery_follows_rebound_endpoint` failing
  (`443 passed, 1 failed`). The test passes in isolation and on the immediately following
  full run (`444 passed`); it is order/timing dependent (shared push-throttle state) and lies
  in a test file that executes **before** `test_phase_f_security.py`, so it is unrelated to
  the Phase F changes. It is recorded here rather than hidden.

No live container was restarted, no live DB row was written, no migration was applied to the
live database.

---

## 11. Files changed

New:

- `backend/alembic/versions/0011_session_revocation.py` — `users.token_version` (idempotent).
- `backend/app/core/limits.py` — `clamp_limit` / `clamp_offset`.
- `backend/tests/test_phase_f_security.py` — 19 regression tests.
- `PHASE_F_GOOGLE_VERIFICATION_AND_READINESS_REPORT.md` (this file).
- `GOOGLE_INTEGRATION_STATUS.md`.

Modified:

- `backend/app/models/user.py` — `token_version` column.
- `backend/app/core/security.py` — `create_token(..., ver=)`.
- `backend/app/api/deps.py` — `token_is_current` + enforcement in `get_current_user`.
- `backend/app/api/auth.py` — `ver` on issued tokens; `POST /api/auth/logout`; version checks
  on `/status` and the admin `register` path.
- `backend/app/services/google_auth.py` — NF‑3 epoch-expiry fix.
- `backend/app/api/{emails,tasks,events,documents,notifications,news}.py` — clamp limits;
  documents upload cap.
- `backend/app/core/config.py` — `API_MAX_PAGE_SIZE`, `MAX_UPLOAD_MB`.
- `frontend/src/lib/api.ts` — `api.auth.logout()`.
- `frontend/src/app/settings/page.tsx` — “Sign out” button.
- `PHASE_E_SECURITY_HARDENING_REPORT.md` — redacted inline DSN examples.

All changes are consistent with existing patterns; no migration, credential, destructive
operation or live/provider action was required to apply them.

---

## 12. Live safety confirmation

- Live DB `ai_chief` still reports Alembic revision **`0009`**; row counts unchanged
  (3 / 4 / 2). No migration, insert, update or delete touched it.
- No live container was restarted or rebuilt; `ai_chief_test` was never used.
- The only provider contact was a **read-only** token refresh probe, not persisted.
- The disposable `phasef-pg` container is isolated from the live stack and can be removed.
- The n8n database was read from a **copy** (including `-wal`/`-shm`), never modified.

---

## 13. Remaining risks / approvals needed

1. **Re-authorize Google (external action).** The stored live credential is dead. Until the
   account owner completes the consent flow for user 1, Gmail/Classroom/Calendar cannot be
   verified or used live. **This is the single blocking item.**
2. **Deploy migration `0011` + backend/frontend changes** to make session revocation and the
   other fixes live. Requires the (out-of-scope) deploy step.
3. **Coursework scope.** After re-auth, confirm the granted scopes include
   `classroom.coursework.me.readonly`; the *current* stored token lacks it.
4. **n8n stale JWT (NF‑7)** — update the live credential to restore the Monitor workflow.
5. **NF‑2 / NF‑4 / NF‑5** — product decisions before implementation.

---

## 14. Closing verdict

**BLOCKED** — real-provider verification of Gmail, Google Classroom and Google Calendar
cannot be completed because the stored live Google refresh token is dead, and re-authorizing
it is an external credential action outside this phase’s permitted scope. The application-side
OAuth flow, authorization, state validation, token storage/refresh, per-service error and
re-auth paths, per-user isolation, and the in-scope security fixes (session revocation,
false-health, bounded pagination, upload cap) are implemented and verified by 444 passing
tests, but those changes are **not deployed** and the live database remains at migration
`0009`. Address items 1–4 in §13 to unblock.
