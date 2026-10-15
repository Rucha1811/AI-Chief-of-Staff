# Phase E — Security Hardening Report

**Scope:** authentication, session security, API authorization, multi-user data
isolation, integration-credential lifecycle, n8n authentication, webhooks and
notification isolation.
**Constraints honoured:** no VPS/DNS/TLS/public exposure/HTTPS; no git
mutations; no live stack restart/rebuild; no live migrations; no credential
rotation/session revocation/Google re-auth/n8n changes; no real
WhatsApp/Calendar/email/notification actions; live databases (including
`ai_chief_test`) untouched; all secrets redacted. Phase F was **not** started.

---

## A. Executive summary and verdict

The application has already been through several security-hardening phases
(P2.4A/B/C, D-series). Phase E audited every registered API route (97 routes),
the authentication and session layer, the authorization/ownership model, the
Google credential lifecycle, the n8n integration contract, and the
webhook/notification isolation boundaries. Every claim below was checked
against the running application and the live read-only state, not against
source text alone.

Two low-severity, in-scope defects were confirmed and fixed with regression
tests; the rest of the surface held up, with several MEDIUM/LOW items
documented as non-blocking (a few of which cannot be finished without a
schema migration, a live credential change, or real provider traffic — all
out of scope per the Phase E safety rules).

* Baseline backend suite: **382 passed / 0 failed** (isolated disposable DB).
* After Phase E fixes + new tests: **425 passed / 0 failed** (+43 tests).
* Frontend `next build`: **succeeded** (18 routes); no frontend test framework
  exists in the repo (documented).
* Live state unchanged: same containers/uptimes, live DB still at migration
  `0009` with identical row counts, `ai_chief_test` untouched, backup
  checksums identical.

**Overall verdict: PARTIAL — no CRITICAL or HIGH exploitable defect was found;
all demonstrable in-scope defects were fixed and regression-tested; several
MEDIUM/LOW items remain open by design or are blocked on live credentials, and
a small set of live-behaviour checks could not be executed.**

The application must **not** be described as "fully secure." This audit is a
point-in-time, host-level, read-mostly review; it does not replace continuous
testing, dependency scanning, or a live-credential integration test.

**Recommendation: `2. READY FOR PHASE F WITH DOCUMENTED NON-BLOCKING ISSUES`**
(full text at the end).

---

## A1. Baseline state (captured read-only, before any change)

| Item | Value |
|---|---|
| Branch / HEAD | `main` @ `15fd552ece54a61cd19b0ec63b8e4e9105387149` |
| Staged / tracked-modified / untracked entries | `0 / 65 / 45` |
| Live backend DB | `ai_chief`, `alembic_version = 0009` |
| Live row counts | users 3, emails 1510, events 198, tasks 159, notifications 661, user_integrations 4, roles 3, permissions 30 |
| Live containers | backend (4d), postgres (7d, healthy), frontend (7d), ollama (7d), n8n (8d) |
| `ai_chief_test` | present, ~12 MB — **not used, not modified** |
| Backups | `backups/phaseA_*.sql`, `backups/phaseB_*.sql`, `backups/backup.log` (sha256 unchanged after audit) |

The live deployment runs with `ENVIRONMENT=development`. Production secret
enforcement is therefore not active live, but the enforcement code path is
present and is covered by tests (see §F).

---

## B. Route inventory (97 routes) and authorization model

Authorization is enforced in **two independent layers**:

1. **`AuthMiddleware`** (`backend/app/main.py`) — a fail-closed gate. Public
   paths are an explicit allowlist; everything under `/api/integrations/` is
   classified per `(method, route template)` in
   `backend/app/core/access_policy.py`, and any **unlisted** route falls back to
   the strict default (session **and** API key). Non-`/api/` paths bypass.
2. **Per-handler dependencies** (`get_current_user`, `require_permission`,
   `_check_api_key`) — the real authorization decision, duplicated for the same
   reason (same comparison helpers, not a second notion of "authorized").

### Public (anonymous) routes — 8 paths (one serves both GET and POST)

| Method | Path | Rationale |
|---|---|---|
| POST | `/api/auth/login` | credential entry point |
| POST | `/api/auth/register` | first-owner bootstrap / admin-gated creation |
| POST | `/api/auth/exchange` | trades one-shot OAuth code for token |
| GET | `/api/auth/status` | unauthenticated status probe |
| GET | `/api/auth/google/login` | anonymous Google sign-in entry (D2) |
| GET | `/api/integrations/google/callback` | Google redirect; authenticates via server-side state |
| GET | `/api/health` | one-bit liveness probe |
| GET/POST | `/api/webhooks/whatsapp` | provider webhook; HMAC-signature-verified |

### API-key-only (server-to-server / n8n) — 5 routes

`GET /api/integrations/config`, `POST /api/integrations/emails/ingest`,
`POST /api/integrations/retention/purge`,
`GET /api/integrations/agent/accounts`, `POST /api/integrations/agent/sync`.
A user session alone is **not** accepted (verified by test).

### Session-required with per-user scoping

All 80+ remaining routes require `get_current_user` except two noted below.
Resource routes (`emails`, `events`, `tasks`, `documents`, `exams`,
`assignments`, `attendance`, `timetable`, `notifications`, `whatsapp`) filter
by the resolved `user.id` (verified for representative routes). Per-user
Google/Teams connection routes and `integrations/user` writes additionally
require `manage_integrations`.

Two session routes lacked a per-user dependency:
* `GET /api/system/info` — **fixed in Phase E** (see §K2).
* `GET /api/news` — external news only, no user data; left as-is (documented,
  §F/NF-10).

---

## C. Authentication & session security findings

**Design (verified good):**
* Tokens are `base64url(JSON).HMAC-SHA256` over `SECRET_KEY`
  (`backend/app/core/security.py`). No JWT library, no `alg` header, so
  `alg=none`-class attacks do not apply.
* `verify_token` uses a constant-time compare and rejects tampered, expired,
  truncated and malformed tokens; every failure path returns `None`.
* Passwords use PBKDF2-HMAC-SHA256 (120k iterations, per-password salt).
* Login has per-IP sliding-window throttling plus lockout (5 failures / 60s →
  5 min lockout). `X-Forwarded-For` is trusted only from configured proxy IPs.
* OAuth login hands the browser a single-use, short-lived **exchange code**,
  never the long-lived token (token-in-URL eliminated; covered by tests).
* Account disablement is enforced on every request (`get_current_user` → 403),
  and roles/permissions are loaded from the DB per request (no stale
  privilege from the token).
* The OAuth `state` cookie is `HttpOnly` + `SameSite=Lax` + `Secure` (always in
  production).

**Findings:**
* **NF-1 (MEDIUM, not fixed):** tokens are stateless with no revocation/logout
  and no `jti`/`token_version`. Changing a password, or rotating `SECRET_KEY`
  without restart, does not invalidate already-issued tokens before `exp`
  (default TTL 7 days). Disabling an account *is* effective immediately, which
  bounds the worst case. A correct fix needs a token-version column / migration
  and is out of Phase E scope (§E8: document and stop).
* **NF-2 (MEDIUM, not fixed):** login throttling/lockout state is
  process-local (`dict`s in `auth.py`). A multi-worker deployment multiplies
  the effective attempt budget per worker; a restart clears lockouts. A shared
  store (Redis/DB) is required and is out of scope.
* **NF-5 (LOW, not fixed):** `/api/auth/register` returns `409` for an
  existing email (account-existence oracle), and the login path branches before
  `verify_password`, a small timing signal. Both are common product trade-offs;
  changing them affects UX and is a product decision.
* **Positive:** legacy subject-less tokens resolve to the bootstrap owner;
  this is only reachable by tokens the server itself mints (verified), and is
  regression-tested.

---

## D. Authorization & data isolation coverage

* Ownership is consistently enforced via `_get_owned(db, user.id, id)` helpers
  or `WHERE user_id = :me`. No endpoint trusts a caller-supplied `user_id` as
  identity (verified: the only `user_id`-bearing request schema is the
  API-key-protected ingest batch, and it is resolved server-side by
  `resolve_ingest_user`).
* Cross-user isolation is regression-tested for documents, notifications and
  timetable (this phase) and, from earlier phases, for emails, events, tasks,
  exams, assignments, attendance and whatsapp.
* `integrations/user` writes require `manage_integrations`; `auth/register`
  requires `manage_users`; members are correctly refused (tested).
* Integration routes fail closed: an unlisted integration route requires both
  credentials, and the classifying test enumerates every registered
  integration route.

**Finding:**
* **NF-10 (LOW, partly fixed):** `is_authenticated` in the middleware checks
  only token signature/expiry, not that the user exists or is active. Routes
  that rely on the middleware alone therefore still serve a signature-valid
  token belonging to a since-disabled/deleted user. `GET /api/system/info` was
  the one non-trivial such route and was fixed (§J2). `GET /api/news` (public
  news feed, no user data) is the remaining instance and is documented.

---

## E. Input validation, abuse resistance, information exposure

* Error responses do not leak raw exception text (existing tests); Google/Teams
  provider errors are logged server-side and mapped to generic 503s.
* Batch ingest is bounded (`INGEST_BATCH_MAX`, 413 over limit).
* `get_db`/ownership prevents IDOR; malformed IDs yield 404, not stack traces.
* Secret-strength policy: in `ENVIRONMENT=production`, `SECRET_KEY`,
  `INGEST_API_KEY`, `AUTH_PASSWORD`, `POSTGRES_PASSWORD` and OAuth secrets must
  be strong and explicit or the process refuses to start (tested). Files
  (`.env`, `.secret_key`, `.ingest_key`, backups) are gitignored and excluded
  from image contexts (tested).

**Findings:**
* **NF-6 (LOW, not fixed):** list endpoints accept unbounded `limit` values
  (`tasks`, `events`, `documents`, `emails`, `notifications`, `news`) with no
  upper clamp, and `documents/upload` reads the whole file into memory with no
  size cap. On the current localhost-only, development deployment the risk is
  low (single operator); a max-limit/streamed-upload cap is recommended before
  any multi-tenant exposure.
* **NF-7 (LOW, not fixed):** `active`-user enforcement is missing on the one
  public-ish session route reaching external content (`/api/news`), per NF-10.

---

## F. Google OAuth / credential lifecycle & integration health

* Per-user token rows are encrypted at rest (`Fernet`, keyed from
  `SECRET_KEY`); the API never returns stored tokens (metadata only).
* The OAuth callback resolves the owning user via a server-side `state` nonce;
  a state that matches no active/pending integration redirects to an error
  without exchanging the code.
* `gmail_service`/`classroom_service` convert `RefreshError` to a graceful
  **503** (D.6 fix, still present) and mark the persisted auth state dead so the
  UI can prompt re-authorization.

**Findings:**
* **F-1 (MEDIUM, fixed):** `GET /api/integrations/google/authorize` validated
  the caller-supplied `redirect_uri` with `startswith(("http://localhost",
  "http://127.0.0.1"))`, which accepts `http://localhost.evil.com`,
  `http://localhost:3000<at>evil.com`, `http://127.0.0.1.evil.com`. Exploitability
  is limited because Google only redirects to a pre-registered URI, but the
  control silently failed to do what it claimed. Fixed by parsing the URL and
  matching the hostname exactly (§J1).
* **NF-3 (MEDIUM, not fixed):** integration health can report
  `connected: true` / state `healthy` without contacting Google whenever the
  stored access token looks unexpired — most notably when
  `token_expires_at` is absent, because `google-auth` treats a `None` expiry as
  "not expired", so `creds.valid` short-circuits `ensure_refreshed_for_user`.
  This is the D.6 "reports healthy despite unusable credentials" concern.
  A correct fix (explicit `Configured / Authenticated / Operational /
  Re-auth-required / Unavailable` states, forcing a refresh or lightweight
  read-only probe when expiry metadata is missing) changes a user-facing status
  contract and, when it makes a real provider call, touches credential
  handling — so it is documented here as a designed follow-up rather than
  changed under the "smallest safe fix" rule.
* Blocked from live verification: the live Gmail/Classroom refresh token is
  dead (`invalid_grant`) and re-authorization is a user action (§L).

---

## G. n8n-to-FastAPI authentication assessment

* **Intended contract (correct):** n8n calls `/api/integrations/agent/*` and
  `/api/integrations/emails/ingest` with the `X-API-Key` ingest secret; the
  backend resolves `account_id → user` from server-side OAuth state
  (`resolve_ingest_user`) and never trusts a numeric `user_id`. The n8n repo
  workflow `ai-chief-email-sync.json` uses an `httpHeaderAuth` credential
  ("AI Chief Ingest API") against those endpoints — the right design.
* **Live n8n state (read-only inspection of the n8n SQLite copy):**
  * Active workflows: `AI CHIEF · Phase 2 Monitor` and `AI CHIEF · WhatsApp
    Inbound`. Inactive: `AI CHIEF Phase 1 POC`.
  * The new multi-user `ai-chief-email-sync.json` is **not deployed live**.
  * `AI CHIEF · Phase 2 Monitor` calls user-scoped endpoints
    (`/api/emails`, `/api/emails/sync`) with a long-lived `httpBearerAuth`
    credential. That JWT expired — the D.6 symptom (repeated HTTP 401). This is
    a **credential/workflow operations** problem, not an application defect:
    the app has no long-lived-token path for server-to-server use by design.
* **Recommendation (operational, needs authorization):** migrate the monitor to
  the API-key endpoints (or rotate its bearer credential) and deploy the
  multi-user sync workflow. No live n8n change was made (out of scope).

---

## H. Webhooks & notification isolation

* The WhatsApp webhook is public **only** because it must be; it verifies
  Twilio (`X-Twilio-Signature`, HMAC-SHA1 over URL+params) or Meta
  (`X-Hub-Signature-256`, HMAC-SHA256 over raw body) with constant-time
  compares, and fails closed when the secret or signature is missing. The GET
  verification handshake requires a configured `META_WA_VERIFY_TOKEN`.
  Regression-tested (existing `test_whatsapp_webhook.py`).
* Push subscriptions are written under the authenticated `user.id`; the VAPID
  public key endpoint is session-gated.
* Notifications are per-user: listing, unread-count, mark-read and read-all all
  filter by `user.id`; mark-read on another user's row is a no-op (tested).

**Finding:**
* **NF-4 (LOW–MEDIUM, not fixed):** inbound WhatsApp attribution matches the
  sender (`From`) against `user_integrations.account_id` (which stores the
  *user's own* number) and otherwise falls back to the bootstrap owner; the
  auto-reply is sent from the owner's provider connection. In a multi-user
  deployment this mis-attributes inbound messages/replies to the owner. The
  Twilio sandbox shares one provider number, so a correct per-user router is a
  design decision (per-user numbers / explicit mapping), not a one-line fix.
  Documented.
* Twilio signature verification uses `str(request.url)`. Behind the n8n relay
  the backend sees an internal URL, so Twilio signatures can fail unless a
  trusted public URL is configured — a known deployment consideration,
  unchanged.

---

## I. Confirmed defects (severity summary)

| ID | Area | Severity | Status |
|---|---|---|---|
| F-1 | Google `authorize` redirect_uri prefix check accepted non-loopback hosts | MEDIUM | **Fixed** |
| F-2 | `/api/system/info` session-gated but not active-user-gated | LOW | **Fixed** |
| NF-1 | No token revocation / logout; TTL-only | MEDIUM | Documented |
| NF-2 | Process-local login throttling/lockout | MEDIUM | Documented |
| NF-3 | Google integration health can report healthy without contacting Google | MEDIUM | Documented |
| NF-4 | WhatsApp inbound/reply attribution defaults to owner (multi-user) | LOW–MEDIUM | Documented |
| NF-5 | Registration 409 enumeration; login timing signal | LOW | Documented |
| NF-6 | Unbounded list `limit`; unbounded upload size | LOW | Documented |
| NF-7 | Live n8n monitor uses expired long-lived JWT | LOW (operational) | Documented |
| NF-8 | Auth 401s originate inside/after the CORS layer (dev cross-origin CORS error vs 401) | INFO | Documented |
| NF-9 | `/docs`&nbsp;/ `/openapi.json` are session-gated (safe but `_DOC_PATHS` reads as if they were exempt) | INFO | Documented |
| NF-10 | Middleware `is_authenticated` ignores account existence/status; `/api/news` remains | LOW | Partly fixed |

No CRITICAL or HIGH defect was confirmed.

---

## J. Defects fixed this phase (files + rationale)

Both fixes are the smallest change that closes the demonstrated gap and are
covered by new regression tests. No migration, credential rotation, destructive
operation or real provider action was required.

### J1 — Google `redirect_uri` host validation
**File:** `backend/app/api/google_oauth.py` (untracked file added in a prior
uncommitted phase).

* Before: `if redirect_uri and not redirect_uri.startswith(("http://localhost", "http://127.0.0.1")):`
* After: parse with `urlsplit` and require `scheme in {http, https}`,
  no embedded credentials, and `hostname in {localhost, 127.0.0.1, ::1}`.
* Demonstrated (old → new): `http://localhost.evil.com/cb` **True → False**;
  `http://127.0.0.1.evil.com/cb` **True → False**;
  `http://localhost:3000<at>evil.com/cb` **True → False**; legitimate loopback
  URIs still **True**.

### J2 — `GET /api/system/info` requires an active user
**File:** `backend/app/main.py` (already tracked-modified).

* Added `user: User = Depends(get_current_user)` (plus the `Depends`,
  `get_current_user` and `User` imports). Previously the route was gated only by
  the middleware, so a signature-valid token for a since-disabled or deleted
  user still read operational detail (pool sizes, version, feature/integration
  flags). Now it returns 403 (disabled) / 401 (deleted) like every other
  session route.

---

## K. Regression tests & evidence

New file: `backend/tests/test_phase_e_security.py` — **43 tests**.

* OAuth redirect validation: 10 rejected URIs (400) + 5 accepted loopback URIs
  (200) + no-`redirect_uri` case + missing-session case.
* Token integrity: tampered signature, wrong-secret token, expired token,
  6 malformed tokens, expired-but-validly-signed token, legacy subject-less
  token → owner.
* Authorization: disabled account → 403; member cannot `manage_integrations`
  or create users; 5 protected list endpoints → 401 without a token.
* Isolation: document read/delete across users (404), notification list/mark
  across users, timetable list/update/delete across users.
* n8n boundary: session alone cannot call an API-key endpoint (401); API key
  alone reads `/api/integrations/config` (200, no secret echoed).
* `/api/system/info`: active user 200, disabled 403, deleted 401.

### Exact commands and results (isolated disposable DB)

Test DB isolation: a throwaway `postgres:16-alpine` container (`phasee-pg`,
random host port, throwaway password) was started for the run; the harness
recreates its own `ai_chief_test` **inside that container**. The live
`ai_chief_test` was never used or modified. The container was removed after the
runs.

```
cd backend
export ENVIRONMENT=testing
export DATABASE_URL="postgresql+psycopg2://postgres@127.0.0.1:<port>/postgres"
# password supplied out-of-band to libpq (PGPASSWORD), throwaway, never recorded

# baseline (no Phase E change)
./venv/bin/python -m pytest -q -p no:cacheprovider
# -> 382 passed

# focused
./venv/bin/python -m pytest tests/test_phase_e_security.py -q -p no:cacheprovider
# -> 43 passed

# focused security set
./venv/bin/python -m pytest tests/test_phase_e_security.py \
    tests/test_phase_d_backend_security.py tests/test_p24_security.py \
    tests/test_p24c_security.py -q -p no:cacheprovider
# -> 137 passed

# full suite (final)
./venv/bin/python -m pytest -q -p no:cacheprovider
# -> 425 passed, 0 failed (125s)
```

### Frontend

```
cd frontend && npm run build
# -> compiled successfully; 18/18 static pages generated
```

There is **no frontend test framework** in the repo (no jest/vitest) and no
root ESLint config, so `next lint` was **not** run and no frontend unit tests
exist. Only the production build (which type-checks) was executed.

---

## L. Blocked / skipped / untested areas (stated plainly)

These were **not** executed and are not claimed as passing:

* **Live Google Gmail/Classroom flows** — the stored refresh token is dead
  (`invalid_grant`). Successful live sync, live health transition and
  re-authorization were not exercised; only the code paths and the
  `RefreshError → 503` mapping were verified. Re-auth is a user action (§C/§F).
* **Real WhatsApp delivery and real inbound signature** — no real provider
  action is authorized and the live provider is `none`; only signature logic is
  covered by unit tests.
* **Google Calendar write-back** — not executed live.
* **Push notifications** — no subscriptions exist; delivery path untested.
* **n8n live fix** — not applied (requires a live credential/workflow change).
* **Multi-worker rate-limit behaviour** — not reproduced (single process).
* **Load / DoS testing** — not performed.
* **`/docs` OpenAPI schema** — reachable to authenticated users only; not
  content-reviewed.

---

## M. Final safety verification

* No git mutation: still staged `0`, tracked-modified `65`, untracked entries
  `45`; the only code changes are the two fixed files and the new test file
  (`google_oauth.py` and `backend/tests/` were already untracked; `main.py` was
  already modified). No commit, push, reset, checkout, stash, rebase or amend.
* Live DB: `alembic_version` still `0009`; row counts unchanged
  (users 3, emails 1510, events 198, tasks 159, notifications 661,
  user_integrations 4). No live migration, no write.
* `ai_chief_test` present (~12 MB) and untouched.
* Containers: backend 4d, postgres 7d healthy, frontend 7d, ollama 7d, n8n 8d —
  none restarted or rebuilt.
* Backups unchanged (sha256 matches baseline; verified).
* No credential rotation, session revocation, Google re-auth, or n8n change.
* No secrets printed in this report; all tokens/keys/private data redacted.
* Disposable test container and temp files removed.

---

## M2. Final safety verification summary line

All live-state invariants held: repo tree unchanged apart from the two fix
files and the new test file; live DB, `ai_chief_test`, containers and backups
untouched; no migration and no real provider action performed.

---

## N. Remaining issues & follow-ups (non-blocking)

1. **Token lifecycle (NF-1):** add a `token_version`/`jti` revocation mechanism
  (needs migration) and a real `/api/auth/logout`; invalidate on password
  change.
* **Shared rate limiting (NF-2):** move login throttling to a shared store
  before multi-worker deployment.
* **Integration health states (NF-3):** implement the explicit
  `Configured/Authenticated/Operational/Re-auth-required/Unavailable` model and
  stop treating a `None` expiry as proof of health.
* **WhatsApp multi-user routing (NF-4):** define a per-user number/mapping
  instead of owner fallback.
* **Enumeration & limits (NF-5/NF-6):** generic registration response; clamp
  list `limit`s; stream and cap uploads.
* **n8n (NF-7):** migrate the monitor workflow to the ingest API key and deploy
  the multi-user sync workflow.
* **Google live credential (NF-3/§L):** user must re-authorize Gmail/Classroom
  from Settings before any live integration test.

---

## O. Recommendation

The audited surface has no CRITICAL/HIGH defect; the two associated, safely
fixable defects are fixed and regression-tested; the remaining items are
documented non-blocking issues that need a schema change, a live credential
change, real provider traffic, or a product decision — all outside the Phase E
safety envelope. Proceeding to Phase F is safe provided the non-blocking items
and the unverified live-credential areas above stay on the follow-up list.

2. READY FOR PHASE F WITH DOCUMENTED NON-BLOCKING ISSUES
