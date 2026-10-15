# Phase D.6 — n8n Workflow Investigation & Integration Verification

## Overall verdict

**PARTIAL**

The investigation is complete and evidence-backed. The real cause of the n8n
failures was corrected in the record (it is **not** Gmail credentials and **not**
a single cause). One genuine application defect was demonstrated and fixed with
regression tests (unhandled `RefreshError` → HTTP 500 on `/api/emails/sync` and
`/api/classroom/sync`). All external-provider paths (Gmail, Calendar write,
Classroom, real WhatsApp delivery, push) remain **BLOCKED — external
credentials/authorization required**, and the live n8n monitor is still failing
pending a credential refresh. No live data, schema, container, or credential was
changed.

---

## A. Baseline and safety

Recorded at the start of D.6 (and re-verified at the end — see K).

| Item | Value |
|------|-------|
| Git HEAD | `15fd552ece54a61cd19b0ec63b8e4e9105387149` |
| Git staged / modified / untracked | 0 / 65 / 44 (status entries) |
| Live DB | `ai_chief @ alembic 0009` |
| Live row counts | users 3 · emails 1510 · tasks 159 · events 198 · notifications 660 · user_integrations 4 |
| Backups | `phaseA_20260913_202145.sql` `4f8a6478…`, `postgres/ai_chief_20261005_150120.sql.gz` `c8e6fff8…`, `n8n/n8n_20261005_150120.sqlite` `d0fd57d4…` |
| Containers | `aichief-backend-1` `74a5e61e2e26` (Up 39h), `aichief-frontend-1` `229c19a1a41c`, `aichief-postgres-1` `a6396d9f9c2a`, `aichief-ollama-1` `c5ba076a58fe`, `n8n-n8n-1` `5ed92ed34414`; all restart count 0 |
| n8n version | 2.36.8 |

**Environment drift discovered since Phase D.5 (not caused by D.6):**

- `user_integrations` went from **0 (D.5)** to **4 rows** now (1 `gmail`, 3 `google`).
- Classroom status changed from `{"connected":false,"reason":"authorization_required"}` (D.5) to `{"connected":true,"reason":"ok"}`.
- The connected Google refresh token is now **dead** (`invalid_grant`), so
  `/api/emails/sync` and `/api/classroom/sync` now return **500** (D.5 saw a
  graceful 503).

**Safety honored:** no commits/branches/reset/clean/checkout/stash; no live-DB
schema change and no Alembic against `ai_chief`; no live container
rebuild/restart; no volume deletion or prune; no credential rotation; no service
exposed publicly; no auth weakened; no external message, calendar write, or
Classroom write performed; no secret value printed. The disposable D.6 test
Postgres was created with a tmpfs data dir (no volume) and removed.

---

## B. n8n workflow inventory

Three workflows exist in the live n8n (read from a read-only copy of the live
SQLite DB; no execution payloads or credentials printed). Repo source of truth:
`n8n/workflows/*.json`.

### B.1 `aichiefPh2Mnt01` — "AI CHIEF · Phase 2 Monitor" (ACTIVE)

- **Trigger:** Schedule Trigger — every **15 minutes** (`minutesInterval: 15`).
- **Nodes (live):** `1 · Sync Emails (Gmail)` (httpRequest), `2 · Fetch Important Emails` (httpRequest), `3 · Normalize Important List` (code), `4 · IF needs attention?` (if), `5 · Summarize Actionable` (code), `7 · Send WhatsApp Digest` (httpRequest), `6 · Log Result` (code), `8 · Logger Output` (noOp).
- **Connections:** Trigger → 1 → 2 → 3 → 4 → 5 → 7 → 6 → 8.
- **HTTP endpoints:** `GET {AI_CHIEF_BACKEND_URL}/api/emails?limit=20` (node 1); `GET …/api/emails?limit=50&category=important` (node 2); `POST …/api/whatsapp/send` (node 7, JSON body `{"to":"","body":"AI Chief update:\n"+…}`).
- **Credentials:** both fetch nodes use `httpBearerAuth` = **"AI Chief Backend JWT"** (a user JWT — not Gmail, not the ingest key).
- **Note:** the node is named "Sync Emails (Gmail)" but it is an HTTP call to the AI Chief backend; it does not call Gmail. The repo template (`ai-chief-phase2-monitor.json`) still points node 1 at `/api/emails/sync`, while the live workflow was patched to `/api/emails` (avoiding the backend's Gmail 503). Node 7 (digest) exists only in the live copy and uses an **empty `to`**.

### B.2 `ZRJGD12Wc04jDO6k` — "AI CHIEF · WhatsApp Inbound" (ACTIVE)

- **Trigger:** Webhook — `POST` path `whatsapp-inbound`, responseMode `onReceived`.
- **Nodes:** `WhatsApp Webhook (Twilio)` → `Relay to AI Chief` (httpRequest).
- **Endpoint:** `POST {AI_CHIEF_BACKEND_URL}/api/webhooks/whatsapp`, `authentication: none`, key-pair body `From/To/Body/MessageSid` (no `X-Twilio-Signature` header forwarded).

### B.3 `xZgJerUVlHJoVwUS` — "AI CHIEF Phase 1 POC" (INACTIVE)

- **Trigger:** Manual.
- **Nodes:** `Manual Trigger` → `1-Sync` (`POST http://host.docker.internal:8000/api/emails/sync?limit=3`) → `2-Read` (`GET /api/emails?limit=3`) → `3-Normalize` → `4-Ingest` (`POST /api/integrations/emails/ingest`, `httpHeaderAuth` = "AI Chief Ingest API") → `5-Report`.

### B.4 Credentials, environment, network

| Credential | Type | Updated | Used by |
|---|---|---|---|
| `AI Chief Backend JWT` | `httpBearerAuth` | 2026-09-12 | Monitor fetch nodes; POC read/sync |
| `AI Chief Ingest API` | `httpHeaderAuth` | 2026-09-04 | POC ingest; agent sync workflow |

- **Env:** `AI_CHIEF_BACKEND_URL=http://backend:8000`; `N8N_BLOCK_ENV_ACCESS_IN_NODE` (false). No per-node secrets baked in.
- **Network:** n8n is attached to the shared `aichief_net`; the service alias `backend` resolves (verified: `GET /api/integrations/agent/accounts` from the backend is reachable, and n8n errors were 401s, not DNS, after 2026-09-25).

---

## C. Failure analysis

All **131** stored executions are `status=error`, all from `aichiefPh2Mnt01`,
and **all fail at the same node** (`1 · Sync Emails (Gmail)`). The count grew
116 → 131 between the D.5 report and now because the schedule runs every 15
minutes. There are **two distinct root causes**, contrary to the D.5
attribution of "Gmail credentials":

| Cause | Count | Window (UTC) | Sanitized error |
|---|---|---|---|
| **Expired backend JWT** | **104** | 2026-10-01 08:30 → 2026-10-08 07:18 | `401 Authorization failed - please check your credentials` / body `{"detail":"Authentication required"}` |
| **Hostname resolution** | **27** | 2026-09-23 19:47 → 2026-09-25 06:59 | `ENOTFOUND The connection cannot be established … incorrect host value` |

Evidence:
- The failing node is an HTTP call that authenticates with the n8n
  `httpBearerAuth` credential "AI Chief Backend JWT". The backend rejects it with
  **401** (decoded from the live execution data; no payloads or secrets printed).
- The ENOTFOUND window predates the `AI_CHIEF_BACKEND_URL` env being set to
  `http://backend:8000`; those requests never reached the backend.
- The backend token TTL is **7 days** (`AUTH_TOKEN_TTL_DAYS` default; live env
  unset). The credential has not been updated since **2026-09-12**, so its token
  is stale/expired.
- This was proven directly against the live backend (read-only `GET /api/emails`):

| Request | Result |
|---|---|
| no `Authorization` | 401 `{"detail":"Authentication required"}` |
| garbage bearer | 401 |
| minted **expired** token (ttl −100s) | 401 |
| minted **fresh** token (ttl 3600s) | **200** |

- `backend/.secret_key` mtime is **2026-08-11** (unchanged), so the 401s are
  **token expiry, not a signing-secret rotation**.
- Corrections to the D.5 record: the failures are not Gmail and are not one
  cause; two causes (68% auth expiry, 32% earlier hostname error).

---

## D. n8n-to-FastAPI tests

Run against the live local backend; **no-write cases only** (no live rows
created — confirmed by unchanged row counts in K).

| # | Case | Result |
|---|---|---|
| 1 | `GET /api/integrations/config` no key | **401** |
| 2 | `GET /api/integrations/config` valid key | **200** |
| 3 | `GET /api/integrations/agent/accounts` no key / bad key | **401 / 401** |
| 4 | `GET /api/integrations/agent/accounts` valid key | **200** `[{user_id:1,email:"admin@aichief.local",account_id:null,status:"active"}]` |
| 5 | `POST /api/integrations/emails/ingest` no key / bad key | **401 / 401** |
| 6 | `POST /api/integrations/emails/ingest` valid key, empty batch | **200** (received 0) |
| 7 | `POST /api/integrations/emails/ingest` malformed JSON | **422** |
| 8 | `POST /api/integrations/emails/ingest` valid key, unknown `account_id` | **404** `{"detail":"Unknown ingest account"}` (no write) |
| 9 | `POST /api/integrations/agent/sync` no key / unknown account | **401 / 404** |
| 10 | Duplicate/idempotent ingest | verified in the isolated suite (`test_n8n_multi_user.py`, `test_ingest_user_aware.py`) — not re-run against live to avoid live writes |

Authentication contract is correct and fail-closed; the ingest key is a
constant-time compared server secret, and identity is resolved server-side from
`user_integrations` (never a trusted numeric `user_id`).

---

## E. Gmail

**BLOCKED — external credential re-authorization required**, and **FAIL
(application defect, now fixed)** for the crash path.

- `POST /api/emails/sync` → **HTTP 500** with an unhandled
  `google.auth.exceptions.RefreshError: ('invalid_grant: Bad Request', …)`
  (traceback through `gmail_service._call_with_retry`).
- `GET /api/emails/auth-status` → `{"connected":true,"reason":"ok"}` and
  `GET /api/emails/health` → `{"connected":true,"state":"healthy","expires_at":null,"has_refresh_token":true,…}` — **falsely healthy**; `expires_at=null` is the tell.
- User integrations: only `user_id=1` has an active Google row with a stored
  token (others are `pending`/`revoked`).
- Not tested: actual Gmail API reads (would require a valid token).

---

## F. Google Calendar

**BLOCKED — would require a real external write / valid credentials; not
performed.**

- There is no dedicated Calendar *read* endpoint; Calendar support is
  write-only via `POST /api/events/sync-to-google`.
- Called with a valid date range and no matching local events →
  **200** `{"created":0,"skipped":0,"failed":0}` (inconclusive: no Google call
  was made because there were no events).
- `POST /api/events/sync-to-google` has a broad `except Exception` → the
  endpoint will not 500, but a credential failure is silently counted as
  `failed` rather than surfaced as "authorization required" (low-severity
  inconsistency — see J).
- A real push test would create a Google Calendar event → **not performed** per
  the write-approval rule.

---

## G. Google Classroom

**BLOCKED — external credential re-authorization required**, and **FAIL
(application defect, now fixed)** for the crash path.

- `GET /api/classroom/status` → `{"connected":true,"reason":"ok"}` (falsely healthy).
- `POST /api/classroom/sync` → **HTTP 500** with the same unhandled
  `RefreshError: invalid_grant` (traceback through
  `classroom_service._call_with_retry`).
- Stored scope set includes the Classroom read scopes, so the failure is the
  dead refresh token, not missing scope.

---

## H. WhatsApp, push and daily briefing

| Channel | Provider / path | Result | Classification |
|---|---|---|---|
| WhatsApp **send** | `POST /api/whatsapp/send` (provider `none`) | 200 `{"ok":false,"provider":"none","skipped":true,"error":"WHATSAPP_PROVIDER not configured"}` — **no message sent** | dry-run behaviour correct; **real delivery BLOCKED** (no provider) |
| WhatsApp **inbound** | Twilio webhook `POST /api/webhooks/whatsapp` | provider `none` → **503** before signature; `GET` verify → **403** | **BLOCKED** + **FAIL (workflow config)**: the n8n relay re-POSTs to the backend URL with no `X-Twilio-Signature`, so even with a provider the Twilio HMAC would not validate |
| Push | `GET /api/notifications/vapid-public-key` | 200 with auth; **0 push subscriptions** | **NOT TESTED / BLOCKED** (no subscriber/device; no real notification sent) |
| Daily briefing | `GET /api/briefing/today` | **200** | **PASS** (endpoint serves; content not printed) |

No real external message or notification was sent.

---

## I. Defects fixed

**Root cause (demonstrated):** a stored Google integration may hold an access
token with unknown expiry (`token_expires_at = NULL`). `google-auth` then treats
the credentials as valid, so `google_auth.ensure_refreshed_for_user` short-circuits
and the dead refresh token is only discovered during the real API call, where
`google.auth.exceptions.RefreshError` (`invalid_grant`) is raised from inside
`request.execute()`. `_call_with_retry` did not catch it, so it propagated as an
unhandled **HTTP 500** (with stack trace) from the sync endpoints.

**Exact files changed:**

1. `backend/app/services/gmail_service.py` — import `RefreshError`; in
   `_call_with_retry`, catch `RefreshError`, call `AuthStateStore.mark_dead(...)`
   and raise `GmailAuthError`. `api/emails.py` already maps `GmailAuthError` → **503**.
2. `backend/app/services/classroom_service.py` — same catch in `_call_with_retry`
   → `ClassroomAuthError` (already mapped to **503**).
3. `backend/tests/test_refresh_error_handling.py` — **new** regression tests
   (untracked; `backend/tests/` is an untracked directory).

**Regression tests (4):** unit coverage that `_call_with_retry` converts
`RefreshError` → `GmailAuthError` / `ClassroomAuthError`, plus API-level checks
that `POST /api/emails/sync` and `POST /api/classroom/sync` return **503** (not
500) when the Google request raises `RefreshError`.

**Results:**

- New tests: **4 passed** (0.28s).
- Full suite (isolated disposable DB, `ai_chief_test`): **382 passed, 0 failed,
  81.72s** (378 before + 4 new).
- No live data, container, or schema was touched. Because the live backend runs
  `uvicorn` **without `--reload`** and bind-mounts source, the fix does **not**
  take effect in the running container until a deliberate restart (not
  performed in D.6).

---

## J. Remaining issues

| # | Severity | Issue | Evidence | Impact | Recommended next action |
|---|---|---|---|---|---|
| J1 | **High (operational)** | n8n Monitor workflow fails every 15 min with **401** | 104 executions 2026-10-01→10-08 | Monitor produces nothing; ~96 failures/day | Refresh the `AI Chief Backend JWT` credential, or (durable) import the repo's ingest-key-only `ai-chief-email-sync.json` and retire/convert the JWT monitor. Requires authorization (live n8n change). |
| J2 | **High (credential)** | Google refresh token dead (`invalid_grant`) | 500 tracebacks; `expires_at=null` | Gmail + Classroom sync non-functional | Re-authorize Google from Settings (user action); then re-verify. |
| J3 | Medium (code, not fixed) | `auth-status`/`health` report `connected/healthy` with a dead token (false health) | `{"connected":true,"reason":"ok"}` despite `invalid_grant` | Misleading UI/ops signal | Make health/auth-status probe the refresh token when `expires_at` is unknown. Outside the approved minimal fix. |
| J4 | Medium (config) | WhatsApp Inbound relay cannot produce a valid Twilio signature (re-POSTs to backend URL, no signature header) | Workflow node config + `verify_twilio_signature` | Inbound WhatsApp can never validate | Decide the canonical webhook owner (Twilio→backend directly, or forward the signature); fix the workflow. |
| J5 | Low (config) | Monitor digest node sends `{"to":""}` (empty recipient) | live node 7 | Digest is a silent no-op/error even when auth works | Populate the recipient or remove the node. |
| J6 | Low (drift) | Repo `ai-chief-phase2-monitor.json` points node 1 at `/api/emails/sync`, live uses `/api/emails`; live has an extra digest node | repo vs live diff | Repo template no longer matches live | Reconcile template with the deployed workflow. |
| J7 | Low | Calendar push swallows credential failures as per-event `failed` (endpoint returns 200) | `calendar_service.push_events_to_calendar` broad `except` | Auth errors look like generic failures | Surface `CalendarAuthError` when the token is dead. |
| J8 | Low (hygiene) | A stray `ai_chief_test` database exists **on the live Postgres** (created 2026-09-13/…, unrelated to D.6) | `ai_chief @ 0009`; `ai_chief_test` present | Clutter; not used by the app | Drop it after authorization (live-DB action — not performed). |
| J9 | Info | `agent/accounts` returns an active Google row with `account_id=null` | `account_id:null` in the response | Weak identity mapping for the multi-user workflow | Populate `account_id` at OAuth connect time. |

---

## K. Final safety verification

Re-verified after all work and teardown:

- **Git:** HEAD still `15fd552e…`; **0 staged**, 65 modified, 44 untracked.
  D.6 additions: `M backend/app/services/{classroom_service,gmail_service}.py`
  and new `backend/tests/test_refresh_error_handling.py`. No commits, resets,
  cleans, checkouts, or stashes.
- **Live DB:** `ai_chief @ alembic 0009`; rows `users=3, emails=1510,
  tasks=159, events=198` — **identical** to baseline (no live writes).
- **Containers:** all live containers `running`, restart count **0**; no live
  container was rebuilt or restarted.
- **Backups:** SHA-256 identical (`4f8a6478…`, `c8e6fff8…`, `d0fd57d4…`).
- **n8n:** inventory unchanged (same 3 workflows, 2 active); execution history
  **not deleted or modified**.
- **Disposable resources:** temporary test Postgres `aichief-d6-testpg` removed
  (tmpfs — no volume); D.6 work directory removed. No volumes deleted, no prune.
- **Live health:** `GET /api/health` → 200.
- **New test data:** created only inside the disposable `ai_chief_test` DB on
  the temporary Postgres; no test data was created in the live `ai_chief` DB.
- **Deviation disclosed:** the live environment had already drifted since D.5
  (Google integrations connected, Classroom `connected:true`, token now dead).
  This predates and is independent of D.6.

---

## L. Recommendation

**2. READY FOR PHASE E WITH DOCUMENTED NON-BLOCKING ISSUES**

The application code is sound, the identified application defect is fixed and
covered by regression tests (full suite **382 passed / 0 failed**), and the
n8n↔FastAPI auth contract is verified. Deployment (Phase E) is not blocked by
code. The remaining items are **external credential / operational** in nature
(re-authorize Google; refresh the n8n JWT or adopt the ingest-key workflow;
configure a WhatsApp provider) and are documented above rather than resolved by
code changes. Features that depend on them (Gmail sync, Classroom sync, the
scheduled monitor, real WhatsApp delivery, push) will not function until those
credentials are restored — none of which requires a code change.

**Stop.** Phase E and any deployment work are not started.