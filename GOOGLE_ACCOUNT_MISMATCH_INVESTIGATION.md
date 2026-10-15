# Google Account Mismatch — Read-Only Investigation Report

**Scope:** read-only. No OAuth initiated, nothing disconnected, no DB writes, no live sync,
no container restarts, no credential/config changes, working tree preserved.

**Observed symptom:** Settings shows Connected account `owner-google@example.com` and status
**Healthy**, while the intended account is `24dcs025@charusat.edu.in`.

---

## 1. Root cause (two independent defects)

### A) The displayed "Connected account" is test pollution served from an on-disk cache
The Settings value does **not** come from Google and does **not** come from the DB.

Display chain:

1. `frontend/src/app/settings/page.tsx:197`
   ```tsx
   {health?.email ?? googleRow?.account_id ?? "—"}
   ```
   `health` comes from `GET /api/emails/health`; `googleRow` from `GET /api/integrations/user`
   (`settings/page.tsx:33-36`).
2. `backend/app/api/emails.py:71-75` → `gmail_service.health_check`.
3. `backend/app/services/gmail_service.py:398-403` — `health_check` sets
   `state["email"] = await get_account_email(...)`, so **`health.email` is the primary source**.
4. `backend/app/services/gmail_service.py:340-372` — `get_account_email` precedence:
   - **`row.account_id` if present** (`:349-351`),
   - else the **memory/disk cache** (`:353-355`),
   - else a live Google profile call (`:357-369`).
5. The disk cache is `backend/cache/account_email_<user_id>.json`
   (`gmail_service.py:300,303-304,307-325`).

Live DB (read-only) confirms row 5 (user 1, holder of the Google credential) has
**`account_id = NULL`**, so step 4 falls through to the disk cache:

```
user_integrations:
 id | user_id | provider | status  | account_id | scopes(...) | token_expires_at
  5 |       1 | google   | active  | <NULL>     | gmail.readonly classroom.courses.readonly
                                                classroom.student-submissions.me.readonly
                                                calendar.events                | <NULL>
```

The cache files on disk (the backend bind-mounts `./backend:/app`,
so these are the exact files the running container reads):

```
backend/cache/account_email.json     -> "24dcs025@charusat.edu.in"   (ts 2026-09-18)  [legacy, now unused]
backend/cache/account_email_1.json   -> "owner-google@example.com"   (ts 2026-10-11T06:24:39Z)
backend/cache/account_email_2.json   -> "member-google@example.com"  (ts 2026-10-11T06:24:39Z)
```

`owner-google@example.com` is **not hardcoded in any app source**. The only occurrences are
test fixtures in `backend/tests/test_gmail_cache_isolation.py:59,85`. Those tests write to the
**real** `backend/cache/` directory because `_monkeypatch_offline_access`
(`test_gmail_cache_isolation.py:40-50`) stubs only `_get_user_service` — it never redirects
`_PROFILE_CACHE_DIR` / `_profile_cache_path`. The test DB's owner user id (1) collides with the
live user id 1, so a test run overwrites the live user 1 cache entry.

**Conclusion:** `owner-google@example.com` is **stale test-fixture data in a runtime cache file**,
not a placeholder, not DB data, not a Google profile value, and not a mis-associated credential.

### B) "Healthy" is a persisted status, not a live provider check — and the running snapshot is stale
`connected` / "Healthy" derives from the persisted `google_auth_state.state == "healthy"`:

- `settings/page.tsx:98` (`connected = health?.connected`), `:202` ("Healthy").
- `backend/app/services/auth_state.py:86-96` → `"connected": row.state == "healthy"`.
- `backend/app/services/gmail_service.py:82-118` (`probe_credentials_for_user`) refreshes
  **only if the credential is considered expired**, then returns the stored state.

Live DB (read-only): `google_auth_state` row 1 = `state=healthy`, `reason=ok`,
`expires_at=NULL`, **`refresh_attempts=0`**, `last_checked_at=2026-10-11T06:55:13Z`.
`refresh_attempts=0` proves **no refresh was ever attempted/recorded** — health was reported
"healthy" purely from stored/derived state.

Why no refresh: `token_expires_at` is NULL. The working-tree fix NF-3
(`backend/app/services/google_auth.py:206-216`) forces a missing expiry to the epoch so the
refresh token is actually exercised. **That fix is uncommitted and was written 2026-10-11 10:39**,
while the backend container started **2026-10-06T17:28:10Z** with `uvicorn` and **no `--reload`**
(`docker-compose.yml`, bind mount `./backend:/app`). It therefore runs the pre-fix code, where a
NULL expiry = "valid forever", so Google is never contacted. (`git show HEAD:...google_auth.py`
contains no `1970`/NF-3 line.)

The stored credential itself is dead: it is the **legacy `token.pickle` import**
(`backend/migrate_legacy_google_token.py:77-82`, `metadata.auth_label = "Legacy Google import"`,
**no `account_id` written**) whose refresh token fails with `RefreshError`, and whose scopes
predate `classroom.coursework.me.readonly`.

---

## 2. Task answers

1. **Frontend source of the email:** `frontend/src/app/settings/page.tsx:197`
   (`health?.email ?? googleRow?.account_id`); status at `:98,:202`. (The home dashboard also
   renders `student_email` at `frontend/src/app/page.tsx:131`.)
2. **Backend chain:** `api/emails.py:71-75` → `gmail_service.health_check`
   (`gmail_service.py:398-403`) → `get_account_email` (`:340-372`); list endpoint
   `backend/app/api/user_integrations.py:37-57`; OAuth callback
   `backend/app/api/google_oauth.py:65-97`.
3. **What `owner-google@example.com` is:** stale **test-fixture** value read from the disk cache
   `backend/cache/account_email_1.json` (causes: NULL `account_id` + tests writing into the live
   cache dir). Not hardcoded, not DB, not Google-derived, not a mis-linked credential.
4. **OAuth state ↔ user binding:** `build_authorization_url` stores a random nonce in the user's
   row (`google_auth.py:328-336`) and returns it; the unauthenticated callback resolves the user by
   matching `user_integrations.meta.oauth_state` (`google_oauth.py:100-114`) and re-verifies with
   `secrets.compare_digest` (`google_auth.py:400-403`). Correctly bound to the logged-in user.
5. **Does a successful callback replace creds / update email?** Yes.
   `save_credentials_for_user` (`google_auth.py:228-258`) reuses the same row, overwrites
   access/refresh tokens, sets `account_id = email`, updates `scopes`, clears `last_error`, sets
   `status="active"`. Caveats: `account_id` is only updated if the identity lookup returns an
   email (`google_auth.py:412-415`); the refresh token is only overwritten if Google returns one
   (`:239-242`) — normally guaranteed by `prompt=consent` + `access_type=offline` (`:348-350`).
5. **Is "Healthy" real?** No — in the current running deployment it is existence/state-based
   (see §1B): `google_auth_state.state`, never re-validated against Google while the credential is
   treated as non-expiring. `auth_status` (`gmail_service.py:375-395`) has the same shape.
6. **Reconnect risk:** No server-side wrong-account linking — the state nonce binds the callback to
   the logged-in user, and the Google account is whichever one the user authenticates as (Google's
   consent screen is forced with `prompt=consent`). A successful reconnect overwrites the old
   tokens and, on identity success, sets `account_id` = CHARUSAT email which takes precedence over
   the cache, correcting the display. **One trap:** see §3.
7. **Expected Classroom scopes:** `backend/app/services/google_auth.py:39-48` includes
   `classroom.coursework.me.readonly` at `:42`. The live row 5 scopes **lack it** (legacy import),
   so a fresh consent must be granted with it.

---

## 3. Is it safe to reconnect the CHARUSAT account through the existing UI?

**Yes — the UI flow is correct and will link to the logged-in user.** But mind the
"disconnect-then-reconnect" trap caused by a distinct defect:

- `disconnect_user_google` sets `row.status = "revoked"` (`google_auth.py:437`) and keeps tokens.
- `_store_pending_state` (`google_auth.py:328-336`) writes the new nonce onto the existing row but
  **does not reset the status**.
- The callback resolver only matches `status in ("active","pending")`
  (`backend/app/api/google_oauth.py:107`), so a **revoked** row is excluded →
   the reconnect is rejected with `?google=error&reason=unknown_state`, leaving the old credential
   row in place.

Because the stale "Healthy" currently makes the UI show **Disconnect** (not "Connect"), the safe
sequence is:

- **Do not click "Disconnect Google" first.** If health first reports not-connected while row 5 is
  still `active` (e.g. after the Phase F deploy), clicking **Connect Google** works normally.
- If the user disconnects first, reconnect can fail with `unknown_state` until the fix below lands.

---

## 4. Smallest recommended fix (NOT implemented — awaiting approval)

1. **Stop tests from polluting the runtime cache (primary).** In
   `backend/tests/test_gmail_cache_isolation.py`, add an autouse/`monkeypatch.setattr`
   redirect of `gmail_service._PROFILE_CACHE_DIR` (and clear `_profile_cache`) to a `tmp_path`,
   so tests can never write `backend/cache/account_email_*.json`. Add a regression assertion that
   no `account_email_*.json` appears in the repo cache dir after the tests.
2. **Make reconnect-after-disconnect work** in `google_auth.py:_store_pending_state`
   (set the row to `pending`, or accept `revoked` in the callback resolver
   `google_oauth.py:107`), plus a regression test for the connect flow.
8. Optional: make `health_check` surface the last real refresh time / verify scopes, so "Healthy"
   cannot be shown from stored state alone.

Operational (not code): the running backend should be restarted onto the working tree **after**
migrations `0010`+`0011` are applied (the model references `users.token_version`), so NF-3 and the
corrected health logic actually run. Removing the polluted `backend/cache/account_email_1.json` is
also sufficient to clear the misleading email immediately.

**Do not** treat `owner-google@example.com` as a real identity or delete row 5 without approval —
row 5 is a real (dead) credential.

---

## 4. Read-only / isolated verification (not run here — live actions were not taken)

```bash
# 1. Confirm the display value is a cache file, not DB/Google
cat backend/cache/account_email_1.json          # -> {"email": "owner-google@example.com", ...}
cat backend/cache/account_email.json            # -> {"email": "24dcs025@charusat.edu.in", ...}

# 2. Confirm account_id is NULL in the live DB (read-only)
docker exec aichief-postgres-1 psql -U postgres -d ai_chief -tAc \
  "SELECT id,status,coalesce(account_id,'<NULL>') FROM user_integrations WHERE provider='google';"

# 3. Prove the test suite writes into the live cache dir (isolated; uses ai_chief_test only).
#    Run from backend/ with the disposable test DB, then inspect the cache files' mtime/email.
pytest tests/test_gmail_cache_isolation.py -q
ls -la cache/account_email_*.json

# 4. Proposed regression tests once a fix is approved
#    - settings/health: account email resolves from row.account_id, never from a stale cache
#    - reconnect-after-disconnect resolves state for a previously revoked row
```

---

## 5. Verdict

- **Account mismatch root cause: confirmed** — `owner-google@example.com` is stale test-fixture
  data in `backend/cache/account_email_1.json`, shown only because live row 5
  (`user_integrations`, user 1) has `account_id = NULL` (legacy `token.pickle` import).
- **"Healthy" root cause: confirmed** — status is persisted `google_auth_state.state`; the
  running backend (started 2026-10-06, no `--reload`) lacks the uncommitted NF-3 fix, so a NULL
  `token_expires_at` is treated as valid forever and Google is never contacted
  (`refresh_attempts=0`).
- **Reconnect via UI: safe** (binds to the logged-in user, overwrites creds, sets `account_id`),
  **except** the disconnect-then-reconnect path, which currently fails with `unknown_state`.

No fix or live action was performed; awaiting approval.

---

## 6. Follow-up investigation: `exchange_failed` (2026-10-11 reconnect attempt)

Read-only investigation. No OAuth initiated, no DB/credentials/config changed, working tree preserved.
Context: user reconnected as `24dcs025@charusat.edu.in` and Google redirected back with
`Google authorization failed: exchange_failed`.

### 6.1 Precise underlying Google error (from backend logs)

```
2026-10-11T08:39:23.410759052Z google code exchange failed for user 1:
Google rejected the authorization code: Scope has changed from
"<8 requested scopes incl. classroom.coursework.me.readonly>"
to
"<7 granted scopes — classroom.coursework.me.readonly omitted>".
```

Google returned a granted-scope set that **omits**
`https://www.googleapis.com/auth/classroom.coursework.me.readonly`. The callback query string
(`scope=email profile … classroom.courses.readonly …`) confirms the scope was dropped at
consent time. The exchange then failed oauthlib's scope-change guard
(`backend/venv/.../oauthlib/oauth2/rfc6749/parameters.py:464-471`), which raises a `Warning`
unless `OAUTHLIB_RELAX_TOKEN_SCOPE` is set (it is not set in the container). That exception is
caught and re-labelled `exchange_failed`. This is the exact provider error — not inferred.

### 6.2 Code path that returns `exchange_failed`

- `backend/app/api/google_oauth.py:91-97` — `try: google_auth.exchange_authorization_code(...)` /
  `except GoogleAuthError as e: logger.warning("google code exchange failed …", e);
  redirect ?google=error&reason=exchange_failed`.
- `backend/app/services/google_auth.py:390-408` — state check (`:402-403`), `_oauth_flow` (`:404`),
  `flow.fetch_token(code=code)` (`:406`) → `except Exception as e: raise GoogleAuthError(
  f"Google rejected the authorization code: {e}")` (`:408`).

### 6.3 Stage of failure

Authorization-**code exchange** → Google token-response scope validation. **Not** redirect-URI
validation, **not** client id/secret selection, **not** HTTP/network, **not** callback state
validation, **not** persistence (the exchange raised before `save_credentials_for_user`; live row 5
is still `pending` with `account_id` NULL and unchanged legacy scopes).

### 6.4 Authorize vs exchange config/redirect URI — consistent

Both `build_authorization_url` (`google_auth.py:339-352`) and `exchange_authorization_code`
(`:390-408`) call `_oauth_flow(...)` (`:317-325`), which calls `_client_config()` (`:135-164`) and
uses `redirect_uri = settings.OAUTH_REDIRECT_URI` (`config.py:645` =
`http://localhost:8000/api/integrations/google/callback`). The frontend does not override it
(`frontend/src/lib/api.ts:554`). Container env `GOOGLE_CLIENT_ID=""` ⇒ both use
`backend/credentials/credentials.json` (`installed`). Google accepted the authorize request and
returned a code, so the redirect URI matched at both stages.

### 6.5 Code reuse / logging / single exchange

- Exchanged exactly once per callback (single `fetch_token`, no retry loop). ✅
- Each attempt used a distinct code (08:09×2, 08:11, 08:39). ✅
- ⚠️ **The authorization `code` and `state` ARE written to the backend logs** by the
  ASGI/uvicorn access logger (INFO) — it logs the full request line including the query string.
  The app's own logger never logs them, the response redirect carries only `google=…`, and the code
  never reaches the frontend. Recommend redacting/disabling access logs for the callback path.

### 6.6 Running snapshot vs current source

The container started 2026-10-06; the OAuth files are newer (working tree, Oct 11), so the snapshot
differs. But the failing path is present in the running code: the logged message
`"Google rejected the authorization code: …"` matches the working-tree string at `google_auth.py:408`,
and the requested scope set quoted by Google matches the working-tree `SCOPES` (incl.
`coursework.me.readonly`, `:39-48`). The stale snapshot is **not** the cause; current source behaves
identically.

### 6.7 Relevance of the reconnect fix (task 8)

The reconnect fix (`_store_pending_state` moving a non-active row to `pending`) is what let the flow
reach the exchange stage, but it is **unrelated to and does not address `exchange_failed`**. Evidence:
the earlier callbacks at 08:09:15 / 08:09:45 / 08:11:05 returned `307` with **no** exchange log
(they failed at `_resolve_user_by_state` → `unknown_state` while row 5 was `revoked`); only the
08:39 attempt (after the row was flipped to `pending`) reached `fetch_token` and hit the scope guard.
Both defects are independent; keep the reconnect fix.

### 6.8 Smallest recommended fix (NOT implemented)

`backend/app/services/classroom_service.py:56-63` already treats
`classroom.student-submissions.me.readonly` as satisfying the coursework requirement, so the
requested `classroom.coursework.me.readonly` is redundant today.

- **Option A (smallest code change):** remove `"https://www.googleapis.com/auth/classroom.coursework.me.readonly"`
  from `SCOPES` (`google_auth.py:42`) + a regression test. Google then grants exactly the requested
  set (no scope change) → exchange succeeds; Classroom features stay enabled via the submissions
  scope. Verify no test asserts the 8-scope list.
- **Option B (config-only):** set `OAUTHLIB_RELAX_TOKEN_SCOPE=1` for the backend (oauthlib gate at
  `parameters.py:469`). Minimal, but tolerates any scope reduction globally.
- **Option C (external, correct long-term):** add `classroom.coursework.me.readonly` to the OAuth
  consent screen (and, for the `charusat.edu.in` Workspace, the admin app-access allowlist) so the
  full requested set is granted. Requires Google Cloud/Workspace console access.

Recommended: **Option A**; use **Option C** if coursework.me is genuinely required.

### 6.9 Read-only verification

```bash
# exact provider error (redact code/state in any captured output)
docker logs --timestamps --tail 400 aichief-backend-1 2>&1 | grep "Scope has changed"

# confirm requested scope + the app's substitute logic
grep -n "coursework.me.readonly\|student-submissions.me.readonly" \
  backend/app/services/google_auth.py backend/app/services/classroom_service.py

# confirm the relax gate is unset
docker exec aichief-backend-1 printenv | grep -i OAUTHLIB || echo "not set"
```

Isolated test once a fix is approved: unit-test `exchange_authorization_code` with a mocked flow
whose token response returns the granted subset, asserting it succeeds (and that
`classroom_service` still builds a service when `student-submissions.me.readonly` is present).

### 6.10 Verdict

- **`exchange_failed` root cause: CONFIRMED** — Google granted a subset omitting
  `classroom.coursework.me.readonly`; oauthlib's scope-change guard rejected the exchange.
- **Overall live-connect status: `BLOCKED`** — reconnection cannot succeed until the requested
  scopes and the actually-granted scopes agree (Option A/B/C above). No fix applied; awaiting approval.

---

## 7. Scope decision + implemented changes (2026-10-11)

### 7.1 Decision: `classroom.coursework.me.readonly` is REQUIRED — Option A NOT justified

The connect flow's `exchange_failed` is caused by Google granting a subset that omits
`classroom.coursework.me.readonly`. The task authorised removing that scope **only if genuinely
redundant**. It is **not** redundant:

- The Classroom v1 discovery document (`https://classroom.googleapis.com/$discovery/rest?version=v1`)
  shows `courses.courseWork.list` accepts **only** `classroom.coursework.me[.readonly]` or
  `classroom.coursework.students[.readonly]`. `classroom.student-submissions.me.readonly` is
  **absent** from that method's authorization list.
- The app calls `courseWork().list()` in `classroom_service.py` (`list_assignments` and
  `list_submissions`) to read assignment titles and due dates.
- Therefore the scope cannot be dropped without breaking coursework/assignment sync, and the
  `student-submissions` scope cannot substitute for it.

**Consequence:** `SCOPES` is unchanged. The connect flow's `exchange_failed` cannot be fixed in-repo
without either removing a required scope (rejected) or weakening oauthlib's scope-change guard
(prohibited). It remains **externally blocked** (see 7.5).

### 7.2 What was fixed in-repo (connect flow)

- **`backend/app/services/google_auth.py`** — added a comment on `SCOPES` documenting that
  `classroom.coursework.me.readonly` is required by `courses.courseWork.list` and cannot be
  substituted. No functional change.
- **`backend/app/services/classroom_service.py`** — `_get_user_service` no longer treats
  `classroom.student-submissions.me.readonly` as a substitute for the coursework scope. It now
  requires both `CLASSROOM_COURSES_SCOPE` and `CLASSROOM_COURSEWORK_SCOPE`, and names the missing
  scope(s) in the server-side error. This makes the failure accurate instead of silently
  building an unauthorised service.

### 7.3 Access-log leak (Step 5) — finding + mitigation

- **Finding:** uvicorn's access log writes the full request line for the OAuth callback, including
  `code` and `state` query parameters (e.g. `GET /api/integrations/google/callback?code=...&state=...`).
  These are single-use, short-lived authorization codes; logging them is a credential-leak risk.
- **Mitigation:** new `backend/app/core/logging_filters.py` provides a route-aware
  `OAuthAccessLogFilter` + `redact_oauth_query` + `install_access_log_redaction`. It redacts
  `code`, `state`, `id_token`, `access_token`, and `refresh_token` on the `/api/integrations/google/`
  and `/api/auth/google/` paths while preserving `scope`. Installed in `backend/app/main.py` at import
  (uvicorn configures logging before the app is imported, so the filter persists).

### 7.4 Tests (disposable PostgreSQL, isolated)

- `backend/tests/test_google_oauth_scopes.py` — scope-contract tests: canonical scopes include every
  required permission; coursework scope not silently removed; `_oauth_flow` requests canonical scopes;
  exchange accepts a full grant and persists; exchange rejects a reduced grant without persisting;
  the oauthlib scope-change guard still raises; the relax escape hatch is unset.
- `backend/tests/test_classroom_scope_validation.py` — `_get_user_service` accuracy: requires both
  Classroom scopes; names the missing scope; does not accept the submissions scope as a substitute.
- `backend/tests/test_access_log_redaction.py` — redaction of `code`/`state`/`id_token`/
  `access_token`/`refresh_token` on the OAuth paths; `scope` preserved; non-OAuth paths untouched.
- **Result:** focused run **17 passed**; full backend suite **465 passed, 0 failed** (~150 s) on a
  disposable `postgres:16-alpine` container. Live DB and `ai_chief_test` were not touched; the
  disposable container was removed after the run.

### 7.5 External requirement (connect flow — still blocked)

Live Google authorization for the **connect** flow cannot succeed until the OAuth consent screen /
Workspace allowlists `classroom.coursework.me.readonly` so the full requested set is granted. This
requires Google Cloud / Workspace console access and is **external to the codebase**. No
`OAUTHLIB_RELAX_TOKEN_SCOPE`, no weakened validation, no Google Cloud/Workspace change was made.

---

## 8. `?error=oauth_failed` — Google login flow (2026-10-11)

### 8.1 Root cause

`?error=oauth_failed` is the **Google login** callback (`backend/app/api/auth.py`
`handle_google_login_callback`), a different handler from the connect flow. It called
`google_auth._oauth_flow(...)` with the default `SCOPES` (all 8, including
`classroom.coursework.me.readonly`). Google granted a subset omitting the coursework scope, so
`flow.fetch_token` raised the same oauthlib scope-change `Warning`, and the handler returned
`/?error=oauth_failed`. **Google login was completely broken by the same scope mismatch.**

### 8.2 Fix: login requests identity-only scopes

Login only authenticates the user (who they are); it does not need Gmail/Classroom/Calendar access.
The integration scopes are granted separately by the Settings "Connect Google" flow. So:

- **`backend/app/services/google_auth.py`** — added `IDENTITY_SCOPES` (`openid`, `userinfo.email`,
  `userinfo.profile`); `_oauth_flow` now accepts an optional `scopes` argument (defaults to `SCOPES`).
- **`backend/app/api/auth.py`** `handle_google_login_callback`:
  - requests `scopes=google_auth.IDENTITY_SCOPES`;
  - **no longer calls `save_credentials_for_user`** — login is authentication only. Persisting
    identity-only creds here would overwrite any existing Gmail/Classroom/Calendar grants with
    identity-only ones (a regression). Integration creds are granted by the connect flow;
  - logs the swallowed exception (the reason, never the token) so `oauth_failed` is diagnosable.

This does **not** weaken oauthlib validation and does **not** touch `SCOPES`. Google grants the
identity scopes (they appear in the granted set in the logs), so **login now succeeds without any
external change**.

### 8.3 Tests

- `test_login_flow_requests_identity_scopes_only` — the login callback requests exactly
  `IDENTITY_SCOPES` and not the coursework scope.
- `test_login_does_not_overwrite_integration_credentials` — an existing integration row with the full
  scope set is unchanged after a Google login.
- Covered by the full suite: **465 passed, 0 failed**.

---

## 9. Final verdict

- **Google login (`oauth_failed`): `FIX IMPLEMENTED AND TESTED`** — login now requests identity-only
  scopes and no longer overwrites integration credentials. Live Google authorization is unverified
  until a separate approved browser retry succeeds, but there is **no external blocker** for login.
- **Google connect (`exchange_failed`): `BLOCKED`** — the coursework scope is genuinely required and
  Google will not grant it until the OAuth consent screen / Workspace allowlists it (external).
- **Overall: `PARTIAL`** — the login path is fixed in-repo and tested; the connect path remains
  externally blocked pending the Google Cloud / Workspace scope grant.

---

## 10. Final report — Google OAuth and Integrations Recovery (2026-10-11)

### 10.1 Root causes confirmed (login vs connect are separate)

- **Google login (`?error=oauth_failed`)** and **Google connect (`exchange_failed`)** are two
  different handlers that shared one defect: both requested the full 8-scope `SCOPES`, and Google
  omitted `classroom.coursework.me.readonly`, so oauthlib's scope-change guard rejected the exchange.
  - Login is now fixed by requesting **identity-only scopes** (`IDENTITY_SCOPES`).
  - Connect remains blocked because the coursework scope is genuinely required (see 10.4).
- **Test pollution (root cause A):** `test_gmail_cache_isolation.py` wrote real files into
  `backend/cache/`, which could serve a stale account email. Fixed and isolated.

### 10.2 Code changes (exact files)

| File | Change | Why |
|---|---|---|
| `backend/app/services/google_auth.py` | added `IDENTITY_SCOPES`; `_oauth_flow(scopes=…)` | login requests identity-only; connect keeps full `SCOPES` |
| `backend/app/api/auth.py` | login callback uses `IDENTITY_SCOPES`, no longer calls `save_credentials_for_user`, logs failures | login must not overwrite integration creds; `oauth_failed` is diagnosable |
| `backend/app/services/classroom_service.py` | `_get_user_service` requires both Classroom scopes, names the missing one | accurate failure instead of a misleading 403 |
| `backend/app/core/logging_filters.py` | new `OAuthAccessLogFilter` + installer | redact `code`/`state`/`id_token`/`access_token`/`refresh_token` on OAuth paths |
| `backend/app/main.py` | call `install_access_log_redaction()` at import | uvicorn configures logging before app import |
| `backend/tests/test_gmail_cache_isolation.py` | redirect cache dir to `tmp_path`; add pollution regression test | stop writing into the real `backend/cache/` |
| `backend/tests/test_oauth_reliability.py` | new: redirect-URI, NF-3 expiry, login-only health, isolation, state binding, calendar scope, Gmail dedup | lock the reliability contract |

### 10.3 Automated tests (commands + counts)

```bash
# disposable PostgreSQL (never the live DB / ai_chief_test)
docker run -d --name phasef-pg -e POSTGRES_PASSWORD=phasefpass123 -p 22580:5432 postgres:16-alpine
cd backend
ENVIRONMENT=testing DATABASE_URL=postgresql+psycopg2://postgres:YOUR_PASSWORD@127.0.0.1:22580/postgres \
  venv/bin/python -m pytest -q
```

- **Full backend suite: 485 passed, 0 failed** (~154 s) on the disposable container.
- Frontend production build (`npm run build`): **clean** (type-check + static generation, 18 pages).
- The disposable container was removed after the run; the live DB was not touched.

### 10.4 Live Google verification (per service)

| Service | Result |
|---|---|
| Gmail | **NOT TESTED** — live calls blocked (dead stored credential) |
| Classroom | **NOT TESTED** — live calls blocked (dead credential + coursework scope not granted) |
| Calendar | **NOT TESTED** — live calls blocked (dead stored credential) |

No real-provider verification was performed; all integration tests are mocked.

### 10.5 Account verification

The intended account was **not** confirmed from live provider data. The live DB shows user 1
(`admin@aichief.local`) with a `google`=pending row and a separate `gmail`=active row, and user 3
(`priya@example.com`) with a `google`=revoked row; `google_auth_state` is `not_authorized` for both.

### 10.6 Configuration blockers (exact remaining external actions)

1. Google Cloud Console → OAuth consent screen: add
   `https://www.googleapis.com/auth/classroom.coursework.me.readonly` to the configured scopes.
2. Google Cloud OAuth client: authorized redirect URI must equal
   `http://localhost:8000/api/integrations/google/callback` exactly.
3. CHARUSAT Workspace admin: allowlist the app for the Classroom coursework scope (API access
   control / third-party app access).
4. Enable the Gmail, Classroom, and Calendar APIs on the project.

### 10.7 Security findings

- **Fixed:** OAuth `code`/`state` no longer appear in application access logs (redaction filter).
- **Fixed:** the account-email on-disk cache can no longer be polluted by tests.
- **Remaining:** the redaction filter is in the working tree but **not active in the running
  process** (started 2026-10-06, no `--reload`); it activates on the next backend restart. Server-level
  access-log configuration may still need a controlled deployment change.

### 10.8 Database and deployment status

- **Live schema:** `0009`. **Unapplied migrations:** `0010`, `0011` (both additive + idempotent).
- **No live state changed.** No migration, no container restart, no credential change, no deploy.
- The backend bind-mounts `./backend:/app` but runs the 2026-10-06 process snapshot, so the fixes
  are **not live** until a restart.
- Deployment plan (backup → migrate → rebuild frontend → restart backend → health checks →
  rollback) is in `GOOGLE_INTEGRATIONS_DEPLOYMENT_PLAN.md`. **Awaiting approval.**

### 10.9 Known limitations (not verified)

- No real-provider verification of Gmail, Classroom, or Calendar.
- No real browser Google sign-in.
- Intended account not confirmed from live provider data.
- n8n Monitor stale JWT (170/170 errored) not fixed here (plan in deployment plan §8).
- Frontend ESLint not configured (build type-check is clean).

### 10.10 Next action

**Complete the Google Cloud / Workspace configuration in 10.6 so `classroom.coursework.me.readonly`
is granted, then re-authorize the account through the app (Settings → Connect Google).** Only after
the token response contains the granted coursework scope can Classroom — and the remaining live
verification — proceed.

### 10.11 Status summary

| Item | Status |
|---|---|
| Google login (identity scopes, no overwrite) | `PASS — AUTOMATED TESTS ONLY` |
| Login preserves existing integration credentials | `PASS — AUTOMATED TESTS ONLY` |
| OAuth state validation + user binding | `PASS — AUTOMATED TESTS ONLY` |
| Redirect-URI validation | `PASS — AUTOMATED TESTS ONLY` |
| Strict scope-change validation | `PASS — AUTOMATED TESTS ONLY` |
| Missing/expired token expiry (NF-3) | `PASS — AUTOMATED TESTS ONLY` |
| Refresh-failure handling | `PASS — AUTOMATED TESTS ONLY` |
| Reconnect after disconnect | `PASS — AUTOMATED TESTS ONLY` |
| Account-email cache isolation | `PASS — AUTOMATED TESTS ONLY` |
| Health-status accuracy (login-only ≠ integration) | `PASS — AUTOMATED TESTS ONLY` |
| Gmail pagination + duplicate prevention | `PASS — AUTOMATED TESTS ONLY` |
| Classroom scope enforcement | `PASS — AUTOMATED TESTS ONLY` |
| Calendar scope enforcement | `PASS — AUTOMATED TESTS ONLY` |
| Multi-user integration isolation | `PASS — AUTOMATED TESTS ONLY` |
| Safe logging / redaction of OAuth codes + state | `PASS — AUTOMATED TESTS ONLY` |
| Frontend integration statuses + build | `PASS — AUTOMATED TESTS ONLY` |
| Google login (real browser) | `NOT TESTED` |
| Gmail live provider | `BLOCKED — USER OR ADMIN AUTHORIZATION REQUIRED` |
| Classroom live provider | `BLOCKED — GOOGLE CONFIGURATION` |
| Calendar live provider | `BLOCKED — USER OR ADMIN AUTHORIZATION REQUIRED` |
| Intended account confirmed from live data | `NOT TESTED` |
| n8n Monitor stale JWT | `FAIL` (plan prepared) |

**Overall: not 100%.** Google login works in automated tests; the intended account is not verified;
Gmail, Classroom, and Calendar have not passed real-provider verification.
