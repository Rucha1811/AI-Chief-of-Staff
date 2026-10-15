# CHARUSAT Attendance Integration + Safe Bug Fixes — Report

**Date:** 2026-10-15 · **Branch:** `main` · **Repo HEAD:** `15fd552` (uncommitted working tree)
**Backend tests:** full suite run on a disposable PostgreSQL (`phasef-pg`, `ai_chief_test`)
**Live database:** untouched (no migration applied, no service restarted by this work)
**Nothing was committed, pushed, deployed, or reset.**

---

## 1. Status summary

| Area | Status | Evidence |
|---|---|---|
| Google identity login / logout | **PASS — tested** | `test_p24_security`, `test_p24c_security`, `test_google_oauth_scopes` |
| Google integration connect/disconnect/refresh | **PASS — tested** | `test_google_oauth_scopes`, `test_google_oauth_reconnect` |
| Google-login provisioning bypass (**bug fixed**) | **PASS — fixed** | 2 previously failing tests now pass + new guard test |
| CHARUSAT e-Governance attendance connector | **PASS — AUTOMATED TESTS ONLY** | `tests/test_egov_attendance.py` (40 tests) |
| — live portal read | **BLOCKED — EXTERNAL ACCESS** | CAPTCHA login; cannot be automated legitimately (see §4) |
| Manual attendance (existing feature) | **PASS — unchanged** | `test_manual_attendance_feature_still_works` |
| Multi-account isolation (connector) | **PASS — tested** | `test_attendance_is_isolated_between_users` |
| Connector ↔ Google independence | **PASS — tested** | `test_disconnect_removes_snapshot_but_not_google` |
| Frontend build / type-check | **PASS** | `npx tsc --noEmit` clean; `next build` succeeded |
| Full backend regression suite | **PASS** | 529 passed, 0 failed (baseline 2 failed / 486 passed) |
| n8n, WhatsApp, push, notifications | **PASS (covered by suite)** | full backend suite green |

---

## 2. Baseline (read-only inspection, before any change)

- Branch `main`, HEAD `15fd552`; 62 modified tracked files and many untracked
  files already present (the working tree is a large in-progress change).
- **Baseline test result: `2 failed, 486 passed` (127 s)** against a disposable
  PostgreSQL. Both failures were real and reproducible.
- The historical `AUDIT.md` (2026-10-01) headline items (Teams global token,
  cross-user push delivery, unauthenticated WhatsApp webhook) were re-checked
  and are **already fixed** in the current tree, so they were left alone.
- No working feature was rewritten. Changes were kept small and isolated.

---

## 3. Bug fixed: Google login could still self-provision accounts

**Symptom (reproduced):**
```
FAILED tests/test_p24c_security.py::test_google_login_cannot_promote_an_arbitrary_first_account
FAILED tests/test_p24c_security.py::test_google_login_cannot_promote_a_second_account_even_with_an_owner
```

**Root cause.** `handle_google_login_callback` still **auto-provisioned**: an
unknown Google email created a new user, and on an empty database the first such
user was promoted to **owner** via the bootstrap claim. Any Google account could
therefore claim ownership or self-admit as a member, bypassing the operator
bootstrap token (P24B-06).

**Smallest safe change.** `backend/app/api/auth.py` — Google login now resolves
an **existing** account only:

- unknown email → `?error=access_denied&reason=not_invited`, **no user created**;
- non-active account → `?error=access_denied&reason=account_inactive`;
- existing active account → unchanged success path (single-use exchange code).

Login still requests identity-only scopes and still does **not** persist
integration credentials, so Gmail/Classroom/Calendar grants are never
overwritten by a login.

**Regression coverage.** The two previously failing tests now pass, and a new
guard test (`test_google_login_still_admits_an_existing_member`) locks in that a
provisioned member can still sign in — so the fix cannot over-correct into
blocking everyone.

> Operational note: new users must be created through the operator/register
> path. An already-provisioned account (including the existing users on this
> deployment) signs in with Google exactly as before.

---

## 4. CHARUSAT e-Governance investigation (read-only)

Portal: `https://support.charusat.edu.in/Egov/home.aspx`

Evidence gathered from a single read-only GET of the public login page:

- ASP.NET **WebForms** (`<form ... action="./home.aspx">`, `__VIEWSTATE`,
  `__EVENTVALIDATION`, `WebForm_FireDefaultButton`).
- Login controls: `txtUserName`, `txtPassword`, **`txtCaptchaInput`**, and a
  CAPTCHA image endpoint **`Captcha.ashx`** (`imgCaptcha`, `captchaLoader`).
- The public page exposes **no** attendance API or data endpoint; attendance is
  only reachable after an authenticated session. No `robots.txt`; no published
  API/integration documentation was found.

**Conclusion.** The login is protected by a **CAPTCHA**, so automated login is
**not permissible** (it would mean defeating a deliberate security control).
There is no official API. Therefore the connector **never logs in** and instead
uses two authorized, user-initiated, read-only flows.

---

## 5. What was implemented

### Chosen method (safe, user-initiated, read-only)

1. **Snapshot import** — the student signs in themselves (solving the CAPTCHA),
   opens their attendance page, saves/copies the HTML; the backend parses it
   offline. **No credentials at all.**
2. **Session sync** — the student signs in themselves and supplies the exact
   attendance URL they are viewing plus their own browser session cookie. The
   backend performs a bounded, read-only GET with that cookie. The cookie is
   stored **encrypted** (Fernet, same `SECRET_KEY` as Google credentials) and is
   never logged, never returned, and never shared between users.

Neither flow submits anything to the portal, edits attendance, or touches
faculty workflows. There is **no login automation and no polling**; refresh is
manual.

### Files

| File | Change |
|---|---|
| `backend/app/models/egov_attendance.py` | **New** — per-user subject-level snapshot table |
| `backend/alembic/versions/0012_egov_attendance.py` | **New** — creates `egov_attendance` (idempotent) |
| `backend/app/services/egov_service.py` | **New** — parser, safe percentage maths, bounded fetcher, orchestration, per-user status |
| `backend/app/api/egov_attendance.py` | **New** — REST routes (all user-scoped) |
| `backend/app/core/config.py` | Added `EGOV_*` settings + fail-closed `egov_allowed_hosts` |
| `backend/app/models/__init__.py` | Registered the new model |
| `backend/app/main.py` | Included the router **before** the manual-attendance router (path precedence) |
| `backend/app/api/auth.py` | Fixed Google-login auto-provisioning (§3) |
| `backend/.env.example` | Documented the `EGOV_*` knobs |
| `frontend/src/lib/api.ts` | Added e-Governance types + `api.egov.*` |
| `frontend/src/components/EGovAttendanceCard.tsx` | **New** — status, connect/import/refresh/disconnect, subject table |
| `frontend/src/app/attendance/page.tsx` | Rendered the card above the manual records |
| `backend/tests/test_egov_attendance.py` | **New** — 40 tests |
| `backend/tests/test_p24c_security.py` | Added the existing-member login guard test |

### Endpoints (all require the normal user session)

- `GET    /api/attendance/egov` — state + last known-good snapshot for the caller
- `POST   /api/attendance/egov/session` — store encrypted cookie + one sync
- `POST   /api/attendance/egov/import` — parse/store a saved attendance page
- `POST   /api/attendance/egov/refresh` — re-read with the stored cookie
- `DELETE /api/attendance/egov` — disconnect (removes this user's snapshot only)

### Data model

`egov_attendance`: `user_id`, `term`, `subject_code`, `subject_name`,
`classes_held`, `classes_attended`, `percentage`, `source_updated_at`,
`synced_at`, `source_reference` — one row per subject, replaced atomically per
successful sync. The connection metadata (encrypted cookie, status, last sync,
last error, attendance URL) lives on the existing `user_integrations` table with
`provider = 'charusat_egov'`, so **no** new connection table was needed and the
Google row is categorically separate.

### Correctness rules for attendance numbers

- A percentage is computed **only** from verified `attended`/`held`; a printed
  value is used only when counts are absent, and is discarded when counts exist.
- Missing values stay `NULL` (never silently `0`); a zero denominator, a
  negative numerator, or `attended > held` yields **no** percentage.
- Duplicate subject rows keep the **last** occurrence (never summed); a
  `Total`/`Grand total` summary row is skipped.
- A recognised table with zero data rows is reported as “connected, no rows”,
  not as an error.

### Reliability & failure recovery

- Timeouts (`EGOV_REQUEST_TIMEOUT`) and **bounded** retries with exponential
  backoff, **only** for timeout/transport/5xx. A rejected session (401/403) or a
  login-page redirect fails immediately (no retry storm).
- On any refresh failure the **last known-good snapshot is preserved** and the
  connection is marked `expired` → **“Sign-in required”** or `error` →
  **“Unavailable”**; the UI shows the previous data with a **stale** stamp and
  the last sync time. No snapshot ⇒ an explicit “not connected / no rows” state,
  never fabricated data.
- SSRF guard: outbound reads are pinned to `EGOV_ALLOWED_HOSTS`; cross-host
  redirects are refused and an empty allowlist fails closed.

---

## 6. Tests

**New connector suite:** `backend/tests/test_egov_attendance.py`
(parsing/normalisation, conservative percentage maths, missing/invalid/duplicate
data, login detection, bounded retries/timeouts, no-retry-on-401, cross-host
redirect refusal, import/session/refresh/disconnect, stale/reauth/unavailable
states, 401 without a token, cross-user isolation, Google independence, and that
the existing manual attendance CRUD is untouched).

**Focused results**

- `tests/test_egov_attendance.py` — **40 passed**
- Google-login fixes: `test_p24c_security` (denial + new member guard),
  `test_p24_security`, `test_google_oauth_scopes`, `test_google_oauth_reconnect`,
  `test_auth` — **all passed**

**Full suite (disposable PostgreSQL, live DB never touched):**

```
cd backend && \
  DATABASE_URL="postgresql+psycopg2://postgres:YOUR_DISPOSABLE_PASSWORD@127.0.0.1:22580/postgres" \
  venv/bin/python -m pytest tests -q -p no:cacheprovider
# => 529 passed, 0 failed (136 s)
```

Baseline on the same disposable DB before this task was **2 failed, 486 passed**
(488 collected). The run now reports **529 passed, 0 failed**: the two failures
are fixed and the 41 added tests (40 connector + 1 Google-login guard) all pass.
No previously passing test regressed.

**Frontend:** `npx tsc --noEmit` clean; `npm run build` succeeded
(`/attendance` route built). `next lint` is not configured in this repo (it only
opens an interactive setup prompt), so it could not be run non-interactively.

---

## 7. Multi-account correctness

- Each user can read/refresh/disconnect **only** their own connection and rows
  (all queries filter `user_id`). `test_attendance_is_isolated_between_users`
  proves one user sees nothing of another’s and cannot disconnect it.
- Disconnecting e-Governance does **not** touch the Google row
  (`test_disconnect_removes_snapshot_but_not_google` asserts the Google token is
  intact). Disconnecting Google cannot erase attendance (different provider).
- A connector failure leaves Gmail/Calendar/Classroom/dashboard fully available
  (the connector is disabled-by-default and independent).
- Tests use the disposable `ai_chief_test` database only; the live `ai_chief`
  database was never reset or migrated.

---

## 8. Deployment steps (require your approval — NOT performed)

1. **Backend.** The compose backend bind-mounts `./backend` into `/app`, so the
   new code is already on disk inside the container; the running process simply
   has not imported it. A **backend restart** reloads it and — because
   `ENVIRONMENT=development` runs `Base.metadata.create_all` at startup — also
   creates `egov_attendance` automatically.
   - Development: `docker compose restart backend` (creates the table).
   - Production: run `alembic upgrade head` (applies migration `0012`) **before**
     deploying; the startup schema gate refuses to serve if the DB is not at
     head. Then rebuild + restart the backend image.
2. **Frontend.** It has no source mount (a baked `next build` image), so the new
   `/attendance` card only ships after `docker compose build frontend &&
   docker compose up -d frontend`.
3. No new environment variables are required; the `EGOV_*` defaults are safe.

Until a restart/migration is applied, `GET /api/attendance/egov` fails because
the table does not exist yet; every other feature is unaffected (the connector is
not on the startup path).

---

## 9. How to actually see your attendance (you must do this part)

Because the portal is CAPTCHA-protected, only you can authenticate. Either:

**A. Import (no credentials):**
1. Sign in to `https://support.charusat.edu.in/Egov/home.aspx` yourself.
2. Open your attendance page, then **Ctrl/Cmd+S → “Webpage, HTML only”** (or
   “View source” and copy all).
3. In AI Chief of Staff → **Attendance → “Import saved page”** → paste → Import.

**B. Session sync (live refresh):**
1. Sign in to the portal yourself and open your attendance page.
2. Copy the page **URL** and your browser **session cookie**
   (DevTools → Application → Cookies → `ASP.NET_SessionId`).
3. In **Attendance → “Connect session”**, paste both → Connect. Use
   **Refresh now** whenever you want an update; reconnect if it says
   “Sign-in required”.

---

## 10. Known limitations / open items

- Live end-to-end read from the real portal is **BLOCKED — EXTERNAL ACCESS**
  (CAPTCHA), so the connector is **PASS — AUTOMATED TESTS ONLY**. The exact
  attendance-table columns are handled generically (header-driven), but the real
  page should be confirmed once with a genuine import — the parser reports a
  clear error rather than inventing rows if the shape differs.
- Session cookies expire; the UI surfaces this as “Sign-in required” and keeps
  the last snapshot.
- No write-back to the portal, by design.
- Nothing was committed. Working-tree changes from before this task are left
  in place.
