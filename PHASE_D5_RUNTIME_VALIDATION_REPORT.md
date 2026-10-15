# Phase D.5 — Production Schema Gate & Hardened Stack — Runtime Validation Report

**Verdict: READY WITH NON-BLOCKING ISSUES**

Scope honored: no git mutations, no live-DB writes, no Alembic against live `ai_chief`, no live-container restart/rebuild, no VPS/DNS/TLS changes, no secret values printed. All validation ran on a disposable stack (`--project-name aichief-d5`, isolated network `aichief_d5_net`, volumes `aichief-d5_*`, loopback ports 8100/3100, postgres unpublished), fully destroyed at the end. This is **not** Phase E.

---

## 1. Executive Summary
A fresh, from-scratch re-validation of the Phase D production schema gate and hardened stack was executed end-to-end: full test suite → static config validation → `--no-cache` image builds → static image security → disposable stack → schema-gate refusal/acceptance → container-hardening inspection → browser E2E → external-integration and AI-pipeline checks → isolation/notifications/failure handling → final-state verification → teardown. Every runtime guarantee the Phase D work claims was observed live. All external-provider paths (Google/Gmail/Classroom, Twilio/WhatsApp delivery, live n8n execution) are legitimately **BLOCKED — external provider/credential unavailable** and are recorded as such rather than fabricated. No blocking defects were found.

## 2. Safety Baseline & Constraints
- Git: `HEAD=15fd552ece54a61cd19b0ec63b8e4e9105387149`, **0 staged**, 65 tracked-modified, 43 untracked — identical before and after. No commits/branches/clean/checkout/stash.
- Live DB: `ai_chief @ alembic=0009`, 22 tables, rows `users=3, emails=1510, tasks=159, events=198` — identical before and after.
- Backups intact: `phaseA_20260913_202145.sql 4f8a6478…`, `postgres/ai_chief_20261005_150120.sql.gz c8e6fff8…`, `n8n/n8n_20261005_150120.sqlite d0fd57d4…`.
- Live containers untouched during this run: `aichief-frontend-1` (Up 3d), `aichief-ollama-1` (Up 3d), `aichief-postgres-1` (Up 3d), `n8n-n8n-1` (Up 4d). `aichief-backend-1` reflects the **single, previously user-approved** restart for the Google-sign-in fix (documented here as intentional; no restart performed by this run).

## 3. Phase D Changes Reviewed (repository, not the prior report)
- `backend/app/core/schema_gate.py` — `GATE_ENVIRONMENTS = {production, staging}`; read-only by construction; refuses when DB revision ≠ repository head; multiple heads fatal; never applies migrations.
- `backend/app/core/config.py` — `_TolerantEnvSettingsSource` + `settings_customise_sources` fix pydantic-settings 2.3.4 JSON-decoding of a plain-string `CORS_ORIGINS`; `enforce_settings` production gate rejects localhost/invalid/empty deployment addresses and enforces secret minimums.
- `backend/app/main.py` — `AuthMiddleware` + `_PUBLIC_PATHS` (includes `/api/auth/google/login`); security headers; `apply_startup_schema_policy`.
- `backend/app/services/{push_service,reminder_service}.py` + `core/timeutil.py` — UTC clock for reminder windows and read-notification purge (D5 fix).
- `frontend/src/app/monitoring/page.tsx` (DEGRADED heuristic), `frontend/src/app/settings/page.tsx` (no server-secret names).
- Tests present: `test_phase_d5_time.py` (8 UTC), `test_phase_d_config_origins.py` (incl. 2 CORS regressions), `test_phase_d_schema_gate.py`, `test_phase_d_backend_security.py`, `test_phase_d_compose_hardening.py`, `test_phase_d_frontend_api.py`, `conftest.py` (fresh-DB bootstrap).

## 4. Test Suite Results
`378 passed, 0 failed` in **114.21s**. The spec's 368 target is stale: 368 + 10 new regression tests (2 CORS env-path + 8 UTC). No test ever connected to the live `ai_chief` database. Suite 100% green ⇒ image validation permitted (no PARTIAL stop needed).

## 5. Static Production Config Validation
`docker compose config` valid for base-only and base+prod overlay. **31/31 structural checks PASS**:
- No source bind-mount on backend; exactly `uploads` / `app_cache` / `vapid_keys` named volumes.
- Postgres publishes **no** ports; backend/frontend/ollama loopback-only (`127.0.0.1:8100→8000`, `127.0.0.1:3100→3000`, `127.0.0.1:11434`).
- Backend `user=10001:10001`, `read_only=true`, `cap_drop=[ALL]`, `no-new-privileges`, `tmpfs /tmp:1777`, `cpus=1.5`, `memory=1536Mi`.
- Frontend `user=1000:1000`, `read_only=true`, `cap_drop=[ALL]`, `no-new-privileges`, tmpfs `/tmp` + `/app/.next/cache`, `cpus=0.5`, `memory=512Mi`.
- Frontend build args contain **only** `NEXT_PUBLIC_API_URL`; isolated network `aichief_d5_net`; all required production URLs supplied explicitly (no silent localhost fallback).

## 6. Production Image Build & Static Image Security
Built `--no-cache`: `aichief-d5-backend f6fc2d353472` (697 MB), `aichief-d5-frontend 58410d88281d` (257 MB, bakes `https://api.example.com`), `aichief-d5-frontend-e2e 63349cf5dfc2` (257 MB, bakes `http://localhost:8100`).
- Backend image: `uid 10001` present; `/app` root-owned; only `uploads`/`cache`/`vapid` writable; **no** `.env*`, `.secret_key`, `.ingest_key`, `credentials/`, `vapid/` key files, `*.db`, tests, or private-key material; the 3 `GOOGLE_CLIENT_SECRET` string hits are legitimate source symbols (`config.py` field, `google_auth.py` reference/message), not values; image `ENV` = only `PATH/LANG/GPG_KEY/PYTHON_VERSION/PYTHON_SHA256`.
- Frontend prod image: bakes `https://api.example.com` (4 chunks); zero server-secret strings; no `.env*`; `NODE_ENV=production` acceptable. The 5 regex "DSN-like" hits are minified-JS false positives (font unicode ranges, `https://a@b` placeholder, debug strings).
- Frontend e2e image: bakes `http://localhost:8100`; zero secret strings.
- No OAuth client material baked into any image (backend `.dockerignore` excludes `credentials/`).

## 7. Local Production-Like Stack & Production Schema Gate
- Disposable postgres on `aichief_d5_net`, volume `aichief-d5_pgdata`, **unpublished** (`5432/tcp` internal only).
- **S1 (rejection):** DB bootstrapped to `0009` → hardened backend refused startup with exactly:
  `SchemaGateError: Schema gate refused to start: the database is at revision 0009 but the repository head is 0010; startup never applies migrations automatically - run 'alembic upgrade head' deliberately after reviewing the migration` (container crash-loops; never auto-migrates).
- **S2 (acceptance):** `alembic upgrade head` → `0010` on the disposable DB → hardened backend **Up**; `/api/health` 200 `{"status":"ok"}`; `/api/auth/status` 200. Gate active for `production` **and** `staging`; live DB never touched.

## 7b. Startup Bootstrap & Writable Paths (verified at runtime)
- Disposable DB bootstrap sequence (migrations carry no `op.create_table`): `Base.metadata.create_all(engine)` then `alembic upgrade <rev>` — succeeded to `0009` and then `0010`.
- Backend runs read-only root with writable state confined to the `uploads`/`cache`/`vapid` named volumes and `tmpfs /tmp`; frontend read-only root with tmpfs `/tmp` and `/app/.next/cache`.

## 8. Container Hardening (runtime, via `docker inspect`)
- Backend: `User=10001:10001`, `ReadOnlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges`, `Memory=1610612736`, `NanoCpus=1.5`, `tmpfs /tmp:1777`, port `127.0.0.1:8100→8000`, mounts = the 3 named `aichief-d5_*` volumes, **no bind mounts**.
- Frontend: `User=1000:1000`, `ReadOnlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges`, `Memory=536870912`, `NanoCpus=0.5`, tmpfs `/tmp` + `/app/.next/cache`, port `127.0.0.1:3100→3000`, image-only.
- Disposable postgres: no published ports (`5432/tcp:null`).
- Live `n8n-n8n-1` loopback-only `127.0.0.1:5678`; live `aichief-ollama-1` loopback-only `127.0.0.1:11435`.

## 9. Frontend & Browser E2E
Against the disposable frontend (`localhost:3100`, baked API `http://localhost:8100`):
- App loads (`AI Chief of Staff`); AuthGate shows "This app is password protected", Google button + password input.
- Owner login via `AUTH_PASSWORD` passphrase succeeds → lands on Settings (owner, role `owner`, Google "Not connected / Not Authorized" as expected).
- Dashboard renders fully (greeting, briefing, emails 0, events, tasks, documents, news, "Google connection needs your attention").
- Monitoring page renders: DB ok, **Ollama ok (qwen2.5:7b, available, circuit CLOSED)**, n8n ingest enabled; the AI Worker Queue shows **DEGRADED** on a zero-job queue (documented heuristic, non-blocking).
- **Zero console errors**; all API calls target `http://localhost:8100`; no secrets in request URLs.

## 10. External Integrations — BLOCKED (no provider/credentials) + Contract Validation
- **Google auth:** anon `GET /api/auth/google/login` → 500 with `GoogleAuthError: Google OAuth is not configured. Set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET or place credentials.json in backend/credentials/` (expected; route is intentionally public; protected endpoints stay protected). `GET /api/integrations/google/callback?...` → 307 (public, state-validated). `GET /api/integrations/google/authorize` and `POST .../disconnect` → 401 anon. `GET /api/dashboard` / `/api/emails` anon → 401. **BLOCKED — external provider/credential unavailable.**
- **Gmail sync:** `POST /api/emails/sync` → 503 `{"detail":"Gmail authorization required. Reconnect Google in Settings."}`. **BLOCKED.**
- **Classroom:** `GET /api/classroom/status` → 200 `{"connected":false,"reason":"authorization_required"}`; `POST /api/classroom/sync` → 503 authorization required. **BLOCKED.**
- **Integration agent:** `GET /api/integrations/agent/accounts` anon → 401; with ingest key → 200 `[]` (no connected accounts). Correct credential separation.
- **n8n → FastAPI contract (direct calls):** `GET /api/integrations/config` valid key → 200; ingest no key → 401; ingest wrong key → 401; ingest bogus `user_id` → 404 `{"detail":"Unknown ingest user"}`; empty batch → 200; oversized batch (101 > 100) → 413; malformed JSON → 422.
- **n8n live execution: BLOCKED** — no n8n API key available to trigger. Inventory recorded read-only from the live n8n DB: 3 workflows — `xZgJerUVlHJoVwUS` "AI CHIEF Phase 1 POC" (inactive), `aichiefPh2Mnt01` "AI CHIEF · Phase 2 Monitor" (active), `ZRJGD12Wc04jDO6k` "AI CHIEF · WhatsApp Inbound" (active); **116 executions, all status `error`** (Phase 2 Monitor Gmail→WhatsApp, failing on unavailable Gmail credentials). n8n 2.36.8.

## 11. AI Pipeline, Multi-User Isolation, Notifications, Failure Handling
- **Ollama (live, via `host.docker.internal:11434`):** model `qwen2.5:7b`, available, circuit CLOSED.
- **AI pipeline:** ingested 2 crafted emails through the n8n endpoint (legacy owner fallback) → 2 jobs enqueued, processed **2 / failed 0**; produced 1 Event ("Sprint planning meeting") and marked email deadlines/summaries; reminder_service then generated the corresponding notifications. (Non-blocking: the model's date interpretation was imperfect — extracted a past year/date — an AI-quality artifact, not a stack defect; also, email deadlines surface as `email_deadline` notifications rather than Task rows, by design.)
- **Multi-user isolation:** teammate (`member`) sees 0 emails/tasks/events; cross-user `GET /api/emails/{owner_id}` → 404; cross-user `POST /api/notifications/{owner_id}/read` → `{"ok":false}`; teammate cannot read owner's notifications. Owner operations succeed.
- **Notifications:** reminder scheduler generated notifications from the UTC window logic; `GET /api/notifications` / `unread-count` work; `POST /api/notifications/{id}/read` owner → `{"ok":true}`; `GET /api/notifications/vapid-public-key` → 200 (push provisionable).
- **WhatsApp:** `POST /api/whatsapp/send` → 200 `{"skipped":true}` with provider `none` (disabled semantics); `GET /api/webhooks/whatsapp` → 403 (verification/provider unavailable). **BLOCKED — external provider/credential unavailable.**
- **Failure handling:** no token `401`; bad token `401`; wrong passphrase `401`; malformed JSON `422`; oversized batch `413`; unknown ingest user `404`; unknown route `401`; public `/api/health` `200`; Gmail/Classroom `503` — all handled without crash or stack leakage.

## 12. Security Logs, Final State & Non-Blocking Issues
- **Security headers** (`/api/health`): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'`. CORS reflects only the configured origin (`http://localhost:3100`); a disallowed origin receives no `Access-Control-Allow-Origin`. No secret value (INGEST_API_KEY / SECRET_KEY / POSTGRES_PASSWORD / AUTH_PASSWORD) appears in backend logs (0 occurrences).
- **Final state:** git HEAD/status identical; live DB `0009` with identical row counts; backup SHAs identical; live stack untouched (frontend/ollama/postgres Up 3d, n8n Up 4d, backend = approved restart only).
- **Teardown:** D5 containers, network `aichief_d5_net`, all `aichief-d5_*` volumes, all `aichief-d5-*` images, and the temporary workspace removed; live `/api/health` still 200.
- **Non-blocking issues:** (1) AI Worker Queue shows DEGRADED on a zero-job queue (cosmetic heuristic); (2) `X-Powered-By: Next.js` header; (3) no HSTS header (expected for internal plain-HTTP; terminate TLS in front); (4) AI date-extraction quality (live model); (5) `cors_origins_problems` echo in config diagnostics; (6) transient scheduler trace / `psycopg2` connection-closed lines during the S1 crash-loop boot; (7) all external-provider items above are BLOCKED by missing credentials, not defects.

---

## Verdict: READY WITH NON-BLOCKING ISSUES
The production schema gate provably refuses `0009` and accepts `0010`; the hardened production images and runtime containers match every claimed control; the full test suite is 100% green; the disposable stack served the complete user journey with zero console errors; and all external-provider paths are honestly recorded as BLOCKED for lack of credentials. No blocking issue blocks promotion.