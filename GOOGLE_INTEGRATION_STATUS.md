# Google Integration Status

**Updated:** 2026-10-11 (OAuth recovery) · **App/repo HEAD:** `15fd552` · **Live DB migration:** `0009` · **Repo Alembic head:** `0011`

One-line status: **Google login is fixed in the working tree (identity-only scopes) and the
integration code is hardened, but live Gmail/Classroom/Calendar remain BLOCKED because the stored
credential is dead and Google does not grant `classroom.coursework.me.readonly`. No live state was
changed and nothing was deployed.**

## Per-service

| Service | App flow | Live provider | Blocker |
|---|---|---|---|
| **Google login** | ✅ fixed (identity scopes), tested (mock) | ⏳ NOT TESTED (browser) | needs a real browser sign-in |
| **Gmail** | ✅ wired, tested (mock) | ⛔ BLOCKED | dead stored credential |
| **Google Classroom** | ✅ wired, scope-enforced, tested (mock) | ⛔ BLOCKED | dead credential **+** Google omits `classroom.coursework.me.readonly` |
| **Google Calendar** | ✅ wired, scope-enforced, tested (mock) | ⛔ BLOCKED | dead stored credential |

## What is confirmed working (automated tests)

- **Google login uses identity-only scopes** (`openid`, `userinfo.email`, `userinfo.profile`) and
  does **not** overwrite/delete existing Gmail/Classroom/Calendar credentials. The login callback
  validates OAuth state and the returned identity, and logs failures safely (reason, never tokens).
- **Login and integration authorization have separate scope sets** (`IDENTITY_SCOPES` vs `SCOPES`).
- **Strict oauthlib scope-change validation is preserved** (`OAUTHLIB_RELAX_TOKEN_SCOPE` is never set).
- **Reconnect after disconnect works** — `_store_pending_state` re-activates a non-active row.
- **Missing token expiry is treated as expired** (NF-3 epoch), never "valid forever".
- **Access-log redaction** — `code`/`state`/`id_token`/`access_token`/`refresh_token` are redacted
  on the OAuth callback paths; `scope` is preserved.
- **Classroom scope enforcement** — `_get_user_service` requires both Classroom scopes and names the
  missing one; the submissions scope is not accepted as a substitute.
- **Calendar scope enforcement** — `_get_user_service` rejects a grant without the calendar scope.
- **Redirect-URI validation** — the dev override is restricted to loopback hosts.
- **Multi-user isolation** — integration rows, credentials, and the account-email cache are per-user.
- **Gmail sync dedup is user-scoped** — one user's message id never suppresses another's copy.
- **Test-pollution defect fixed** — `test_gmail_cache_isolation.py` no longer writes into the real
  `backend/cache/` (the root-cause-A mechanism). Two stale cache artifacts were removed.
- Full backend suite **485 passed, 0 failed** on a disposable PostgreSQL; frontend production build
  clean (type-check + static generation).

## What is blocked (needs the account owner / Google admin)

- The stored user-1 refresh token is dead (`RefreshError`). Re-authorizing through the running app
  (log in → **Settings → Connect Google** → grant all scopes) is required.
- **Google does not grant `classroom.coursework.me.readonly`** even though it is requested. The
  Classroom v1 discovery document confirms `courses.courseWork.list` (used by
  `list_assignments`/`list_submissions`) is authorized **only** by a coursework scope, so it cannot
  be dropped or substituted. This is a Google Cloud / Workspace configuration matter (see below).
- Live browser sign-in has not been performed.

## Configuration blockers — exact remaining external actions

1. **Google Cloud Console → OAuth consent screen**: ensure
   `https://www.googleapis.com/auth/classroom.coursework.me.readonly` is in the configured scope
   list (and that the app's publishing status / test-user list includes the account).
2. **Google Cloud OAuth client**: the authorized redirect URI must equal
   `http://localhost:8000/api/integrations/google/callback` exactly.
3. **CHARUSAT Workspace admin** (if the account is on a managed domain): allowlist the app for the
   Classroom coursework scope via the Workspace admin console (API access control / third-party app
   access). Without this, Google silently drops the scope.
4. **API enablement**: Gmail, Classroom, and Calendar APIs must be enabled on the project.

## Security findings

- **Fixed:** OAuth `code`/`state` were written to uvicorn access logs; now redacted by
  `backend/app/core/logging_filters.py` (installed at import in `main.py`).
- **Fixed:** the account-email on-disk cache could serve a stale address (root cause A); the test
  that wrote into the real cache dir is now isolated.
- **Remaining (deployment-level):** the redaction filter is in the working tree but **not active in
  the running process** (started 2026-10-06, no `--reload`). It becomes active on the next backend
  restart. Server-level access-log configuration may still need a controlled deployment change.

## Database and deployment status

- **Live schema:** `0009`. **Unapplied migrations:** `0010` (app_config), `0011` (users.token_version).
- **No live state was changed.** No migration, no container restart, no credential change, no deploy.
- The backend container bind-mounts `./backend:/app` but runs the 2026-10-06 process snapshot, so
  the fixes are **not live** until a restart.
- A reviewed deployment plan (backup → migrate `0010`→`0011` → rebuild frontend → restart backend →
  health checks → rollback) is in `GOOGLE_INTEGRATIONS_DEPLOYMENT_PLAN.md`. **Awaiting approval.**

## Known limitations (not verified)

- No **real-provider** verification of Gmail, Classroom, or Calendar (all live calls blocked).
- No **real browser** Google sign-in.
- The **intended account** was not confirmed from live provider data.
- n8n Monitor stale JWT (170/170 errored) is **not** fixed here; plan in the deployment plan §8.
- Frontend ESLint is not configured (the `lint` script is interactive); the production build
  (which includes type-checking) is clean.

## Next action

**The single most useful action:** complete the Google Cloud / Workspace configuration in
"Configuration blockers" above so `classroom.coursework.me.readonly` is granted, then re-authorize
the account through the app (Settings → Connect Google). Only after the token response contains the
granted coursework scope can Classroom (and the remaining live verification) proceed.

See `GOOGLE_ACCOUNT_MISMATCH_INVESTIGATION.md` for the full root-cause evidence and
`GOOGLE_INTEGRATIONS_DEPLOYMENT_PLAN.md` for the deployment steps.
