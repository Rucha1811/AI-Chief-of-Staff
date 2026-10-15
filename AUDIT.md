# AI CHIEF OF STAFF — COMPLETE REQUIREMENTS & ARCHITECTURE AUDIT

Date: 2026-10-01 · Scope: entire repo (backend, frontend, n8n, docker, scripts, tests) · Method: source-code inspection only; comments/TODOs were **not** treated as implemented. Every claim below was verified against actual code.

---

## 1. STATUS

| | |
|---|---|
| **Overall** | PARTIAL — core multi-user isolation is **well built and tested**, but 1 critical control gap, 2 high, and several medium/low issues remain before multi-user is safe to ship. |
| **Test suite** | ✅ **33 passed, 20 warnings (7.45s)** — ran against the current code in the docker backend container (Postgres). Reproducible command included in §10. |
| **Highest-severity items** | 1 CRITICAL (Teams global-token access), 2 HIGH (push cross-user delivery, WhatsApp webhook unauthenticated), 1 HIGH (n8n is hard single-user) |
| **Code modified by this audit** | **NONE. This audit was read-only.** |

Headline: the per-user model (users/roles, ownership columns, per-user Google OAuth with per-user encrypted tokens, per-user sync loops, per-user CRUD in 15 of 16 routers) is real and correct. The gaps are concentrated in: teams (no per-user auth), push delivery loops, the WhatsApp webhook, the legacy global token path, global unique keys, and the n8n integration (single-user by design).

---

## 2. COMPLETE REQUIREMENTS MATRIX

Legend: ✅ PASS · ⚠️ PARTIAL · ❌ FAIL · ➖ N/A (see note)

| # | Requirement | Status | Evidence (file:line) |
|---|---|---|---|
| R1 | Every user-facing data table has a `user_id` ownership column | ✅ | migrations 0003 (ownership), all models; `emails`/`events`/`tasks`/`exams`/`assignments`/`attendance`/`timetable`/`documents`/`briefings`/`notifications`/`whatsapp_messages`/`google_auth_states`/`user_integrations` all carry `user_id` |
| R2 | Web login: email+password with per-user identity | ✅ | `auth.py:147-164` (`verify_password`, token `sub=user.id`) |
| R3 | Passwords hashed, not stored plaintext | ✅ | `security.py` PBKDF2 (`hash_password`/`verify_password`); migration hashes |
| R4 | Login brute-force protection | ✅ | `auth.py:36-64` per-IP rate limit + fail-streak lockout (5/60s, 300s lock) |
| R5 | Auth fail-open risk eliminated | ⚠️ | `auth.py:24 AUTH_ENABLED = bool(AUTH_PASSWORD)`; `auth.py:77-78` `if not AUTH_ENABLED: return True` — a deploy without `AUTH_PASSWORD` silently disables auth |
| R6 | Roles & permissions model | ✅ | roles/permissions tables (migrations 0002), `require_permission` in `deps.py` |
| R7 | Authorization enforced on **all** sensitive routes | ⚠️ | 15/16 routers use `get_current_user` + scoped filters; **`teams.py` (all 4 routes) has no user dependency** (❌) |
| R8 | Per-user Google OAuth (web flow) | ✅ | `google_auth.py:514-637`, `google_oauth.py`; state nonce per user stored server-side, verified with `compare_digest` (`google_auth.py:599`) |
| R9 | Google tokens encrypted at rest per user | ✅ | `google_auth.py:437-443` (`encrypt_bytes(..., settings.SECRET_KEY)`), per-user row `user_integrations` |
| R10 | Per-user token refresh + write-back | ✅ | `google_auth.py:466-511` (`ensure_refreshed_for_user`) |
| R11 | Legacy global token removed from active paths | ⚠️ | Active legacy path remains: `/api/emails/re-auth` desktop flow (`emails.py:68-73`, owner-only), `load_credentials()` at startup (`main.py:217-222`). Dead-end, owner-scoped, but still present |
| R12 | Per-user Gmail sync | ✅ | `main.py:122-142` iterates all connected users, per-user try/except; `sync_emails(..., user_id=)` |
| R13 | Per-user Classroom sync | ✅ | `main.py:145-161`; `classroom_service.sync_*` scoped by `user_id` |
| R14 | Per-user email ingest | ⚠️ | `email_ingest.py:51` **always stamps `owner_user_id`** — the n8n ingest path is single-user |
| R15 | Per-user emails API (list/get/reclassify/stats) | ✅ | `emails.py:92,110,151,166-177,194` all filter `Email.user_id == user.id` |
| R16 | Per-user events, free slots, conflicts, calendar push | ✅ | `events.py:64,88,101`; free-slots/conflict deterministic + per-user (this session) |
| R17 | Per-user exams / assignments / tasks / timetable / attendance / documents | ✅ | all filter/scope by `user.id` (verified: exams.py:41,54,68; assignments.py:47,60,74; tasks.py `_get_owned`; timetable.py:54,69,83; attendance.py:30,43; documents.py `_get_owned`) |
| R18 | Per-user notifications (DB layer) | ✅ | list/unread/mark-read all `Notification.user_id == user.id` (`notifications.py:40,57,89,103`) |
| R19 | Per-user **push delivery** | ❌ | `push_service.py:171-184`: sends **every** user's high-priority notifications to **all** subscriptions — no `user_id` join |
| R20 | Per-user briefing & dashboard | ✅ | `briefing.py:136,148`; `dashboard.py:27` (`uid = user.id`) |
| R21 | Per-user Teams/MS 365 integration | ❌ | `teams.py` routes 10-38: no `get_current_user`; `teams_service.py:241` stamps events to owner |
| R22 | Per-user WhatsApp (send/connect/messages) | ✅ | `whatsapp.py:22-99` all per-user; inbound `save_inbound` maps `From`→user via `user_integrations` |
| R23 | WhatsApp webhook authenticity | ❌ | `whatsapp.py:102-167` no Twilio `X-Twilio-Signature` nor Meta `X-Hub-Signature-256` check; verify token compared with `==` (`whatsapp.py:174`), default `aichief` |
| R24 | AI pipeline preserves user identity | ✅ | `email_processor.py:270` snapshots `email.user_id`; created Exam/Assignment/Event rows stamped `user_id=user_id`; dedup checks user-scoped (`:55-86`) |
| R25 | Ollama is local, no external AI data exfiltration | ✅ | `docker-compose.yml:33` defaults to `host.docker.internal:11434`; `ai_service.py` calls local ollama only |
| R26 | n8n multi-user email ingestion | ❌ | all 3 n8n workflows use a **fixed single Gmail credential**, no user identity, no retries (see §6) |
| R27 | Global caches keyed by user | ⚠️ | `gmail_service.py:302-324` `get_account_email` uses a global unkeyed module+disk cache (1h TTL) |
| R28 | Ingest endpoints authenticated server-to-server | ✅ | `/api/integrations/*` each route checks `INGEST_API_KEY` via `verify_integration_key` → `compare_digest` (`security.py:66-71`; `integrations.py:22-24`) |
| R29 | Ingest disabled by default | ✅ | `config.py:77 INGEST_API_KEY=None` → endpoints 401 until explicitly configured |
| R30 | Migration coverage for multi-user | ✅ | alembic 0001–0006: baseline, roles, ownership cols, user_integrations, per-user oauth state, per-user events |
| R31 | Isolation test suite | ✅ | `tests/test_isolation.py`, `tests/test_per_user_sync.py` (A-vs-B matrix), plus auth/integrations tests |
| R32 | Frontend supports per-user connection without shared account | ✅ | Settings page (`frontend/src/app/settings/page.tsx`), `api.ts` per-user methods, sidebar link; legacy re-auth buttons removed |
| R33 | No hardcoded / weak secrets shipped in app code | ⚠️ | several defaults remain (see §4): `META_WA_VERIFY_TOKEN=aichief`, dev DB password, `SECRET_KEY` fallback string, plaintext creds in scripts |
| R34 | Public attack surface minimal | ⚠️ | `/api/webhooks/whatsapp` public (needed), `/api/integrations/google/callback` public (stateless), **`/docs` + `/openapi.json` public & unauthenticated** (`main.py:47`) |

**Count:** ✅ 22 · ⚠️ 7 · ❌ 4 · ➖ 0

---

## 3. FINDINGS — ORDERED BY SEVERITY

### CRITICAL

**C1. Teams integration is global and owner-controlled, reachable by any authenticated user**
`api/teams.py` (38 lines) — `/status`, `/connect`, `/connect/poll`, `/sync` have **no `get_current_user`**. The global `AuthMiddleware` (`main.py:50-63`) only checks *a* token exists. Any member who can log in can:
- read the app's global Microsoft 365 auth status (`teams_service.status()`),
- start/abort the owner's device-flow login (`start_device_flow` / `poll_device_flow`),
- trigger sync that writes to the **owner's** calendar (`teams_service.py:241` `user_id=owner_user_id(db)`).
Fix: `Depends(get_current_user)` on all 4 routes + per-user MS account storage (mirror `user_integrations`).

### HIGH

**H1. Cross-user push notification delivery**
`push_service.py:171-184` `push_pending_notifications()` selects **all** subscriptions (`subs`, no filter) and for each high-priority notification **of every user** (`notifs`, no filter) sends it to **every subscription**. `save_subscription` does store `user_id` (`push_service.py:105`), but delivery ignores it entirely. User B's browser receives user A's "Overdue: …" alerts. Web-push payloads are only visible to the holder of the VAPID key but content still crosses users.
Fix: `SELECT * FROM push_subscriptions WHERE user_id = n.user_id` per notification (or join).

**H2. WhatsApp webhook is unauthenticated**
`api/whatsapp.py:102-167`: public POST (in `_PUBLIC_PATHS`, `main.py:41`). No Twilio `X-Twilio-Signature` validation, no Meta `X-Hub-Signature-256` validation. Anyone can POST a body and:
- inject inbound messages attributed to arbitrary sender numbers (stored per-user, spillage risk),
- **trigger outbound replies** (`reply_to_inbound`, `whatsapp.py:121-124,140-142,162-165`) → message-spam/abuse vectors with cost implications.
Verify-token check uses string `==` (`whatsapp.py:174`) and defaults to `aichief` — predictable.
Fix: verify provider signatures (Twilio HMAC-SHA1; Meta HMAC `sha256` over raw body) before processing.

**H3. n8n email ingestion is hard single-user**
All n8n workflow builds (`n8n/build_workflow/*.js`, shipped `n8n/workflows/*.json`) run fixed input nodes against a single Gmail credential and POST to `/api/integrations/emails/ingest`, which stamps **every** email to the owner (`email_ingest.py:51`). There is no user dimension in n8n at all (no per-user credential/variable, no user id in payloads, no retry config). Multi-user ingestion is impossible with the shipped n8n configuration. (Also see §6.)

### MEDIUM

**M1. Gmail account-email exposes another user's address**
`gmail_service.py:302-324` `get_account_email()` caches the resolved account email in a module-global dict + disk file `cache/account_email.json` (1h TTL), **unkeyed by user**. Dashboard/health renders this for the requesting `user_id`, so one user can briefly see whichever user was cached last. Info leak (low sensitivity, real).

**M2. Legacy global Google token path still exists in active code**
`/api/emails/re-auth` (`emails.py:68-73`) still runs the *global* desktop OAuth flow writing the shared `token.pickle` (`google_auth.py:103-192`); startup probes legacy state (`main.py:217-222`) and imports the legacy token to the owner (`main.py:224-238`). Owner-only checks prevent cross-user compromise, but the shared-token abstraction persists and can confuse/overwrite during a reconnect-by-owner.

**M3. Emergency codes are global, not account-scoped (despite the claim in code)**
`auth.py:134` comment says "remain account-scoped to the owner" but `emergency_codes.py:11-58` has **no user binding at all**: any valid code logs the caller in as the owner (`auth.py:139` `_legacy_sub`). The model's `created_by` is a free-text nullable string (`models/emergency_code.py:14`) — no FK. Generate/consume are also un-scoped; any code is a master backdoor to the owner account.

**M4. Global unique keys can silently drop other users' rows**
- `Notification.fingerprint` is UNIQUE (dedup in `reminder_service.py:39-44` is unscoped by user);
- `WhatsappMessage.message_id` UNIQUE, `Email.message_id` UNIQUE (global).
Two users receiving identical messages (e.g., a broadcast deadline or the same TeacherMsg) → the second insert collides and is dropped.

**M5. N8n ingest is owner-only, so member accounts can't get n8n emails at all**
Covered by H3; listed separately because "silently single-user" is a functional gap for every non-owner account.

**M6. Ingestion API key is a single shared secret for every provider**
`integrations.py:22-24` — one `INGEST_API_KEY` for config/ingest/purge with no per-provider or per-endpoint scoping; a leaked key grants full ingest + destructive retention purge. (Properly compared with `compare_digest`, and off by default.)

### LOW

**L1. `SECRET_KEY` weak fallback string**
`config.py:76` default `"change-this-to-a-random-secret-key"`; `config.py:11-35` mitigates by generating + chmod-0600 file on first boot, but if env is unset and file missing the fallback string would be used silently (encrypting tokens/Google creds with a known key). Add a hard startup check.

**L2. `/docs`, `/redoc`, `/openapi.json` are unauthenticated**
`main.py:47,54-59` — full API schema public. Dev-convenient; surface it in any non-dev deploy (reverse-proxy ACL or auth-gate).

**L3. Auth fail-open** (`auth.py:24,77-78`) — deploying without `AUTH_PASSWORD` leaves the whole API authenticated. Confirmed behavior, not theoretical.

**L4. Naive-UTC datetimes throughout**
20 `datetime.utcnow()` DeprecationWarnings; naive `DATETIME` comparisons (e.g., `reminder_service.py`, `gmail_service.py:260`). Not an isolation bug, but a correctness/timezone hazard (DST offsets, comparisons across servers).

**L5. default `WHATSAPP_SEND_TO=+919426666233`** in `docker-compose.yml:45` — a real-looking number shipped as the default automation target.

**L6. Teams events owned by owner even when synced on demand** (`teams_service.py:241`) — consequence of C1/M-missing per-user binding.

---

## 4. HARDCODED / WEAKENED DATA (file:line)

| Severity | Location | Value / Issue |
|---|---|---|
| HIGH | `docker-compose.yml:48`, `backend/app/core/config.py:143` | `META_WA_VERIFY_TOKEN` default **`aichief`** — predictable webhook verify token |
| HIGH | `n8n/README.md` | documents a live app/admin password `aichief` in committing files |
| MEDIUM | `docker-compose.yml:3-7` | `POSTGRES_USER`/`PASSWORD` default `postgres/postgres` (dev default, host port 5434) |
| MEDIUM | `docker-compose.yml:45` | `WHATSAPP_SEND_TO` default `+919426666233` (real number) |
| MEDIUM | `scripts/migrate_sqlite_to_pg.py:5` | plaintext `postgres:postgres@localhost` dsn committed |
| LOW | `backend/alembic.ini:3` | plaintext dsn fallback (dead — `env.py` overrides from `DATABASE_URL`) |
| LOW | `backend/app/core/config.py:76` | `SECRET_KEY` fallback `change-this-to-a-random-secret-key` |
| LOW | `docker-compose.yml:33` | `OLLAMA_BASE_URL` default `host.docker.internal:11434` (dev-only; fine locally, wrong for cloud) |
| LOW | `frontend` | `NEXT_PUBLIC_API_URL` default `http://localhost:8000` (dev-only) |

---

## 5. MULTI-USER ISOLATION MATRIX (verified in source)

| Data group | Read scoped? | Write scoped? | Owner bridge still used | Verdict |
|---|---|---|---|---|
| Emails | ✅ `user_id` filters throughout | ✅ stamped from sync/ingest source | ⚠️ only in n8n ingest | ✅ core, ⚠️ ingest |
| Events | ✅ | ✅ | — | ✅ |
| Exams | ✅ | ✅ | — | ✅ |
| Assignments | ✅ | ✅ (incl. classroom sync) | — | ✅ |
| Tasks | ✅ | ✅ | — | ✅ |
| Attendance | ✅ | ✅ | — | ✅ |
| Timetable | ✅ | ✅ (incl. week-scheduler) | — | ✅ |
| Documents | ✅ | ✅ | — | ✅ |
| Daily briefings | ✅ | ✅ | ⚠️ startup generates owner's only | ✅ |
| Notifications (DB) | ✅ | ✅ | — | ✅ |
| Push subscriptions | ✅ stores `user_id` | ✅ | — | ⚠️ **delivery global** |
| WhatsApp messages | ✅ | ✅ | ⚠️ unknown `From` → owner | ✅ |
| Google auth state | ✅ per-user row | ✅ | ⚠️ legacy no-`user_id` calls | ✅ |
| User integrations | ✅ per-user | ✅ + `require_permission` on manage | — | ✅ |
| Emergency codes | ❌ not per-user | ❌ | — | ❌ global |
| Email account cache | ❌ global cache | — | — | ❌ leak |
| Teams/MS 365 | ❌ global | ❌ global | ✅ owner | ❌ critical |
| n8n ingest | ❌ owner-only | ❌ | ✅ | ❌ single-user |

---

## 6. INTEGRATION AUDIT

### Google Workspace (Gmail / Classroom / Calendar) — PASS with notes
- Per-user web OAuth ✅; state nonce stored in the user's `user_integrations` row and verified with `secrets.compare_digest` (`google_auth.py:525-533,599`).
- Tokens encrypted at rest per user (`google_auth.py:437-443`); refresh writes back encrypted (`:496-503`).
- Least-privilege scopes (`google_auth.py:40-49`): gmail.readonly, classroom *.me.readonly, calendar.events (create/read only), openid+userinfo identity.
- Callback is stateless/public, resolves the user by state (`google_oauth.py:53-64,65-79`); authorize requires auth (`:17`).
- Startup sync loops iterate per-user with isolated failures (`main.py:122-161`).
- Notes: legacy desktop-flow `re-auth` endpoint remains for owner (M2); unkeyed account-email cache (M1).

### WhatsApp — send paths PASS, webhook FAIL
- send/connect/disconnect/messages all per-user (`whatsapp.py:19-99`).
- Inbound routing: `From` → `user_integrations.account_id` → user; unknown senders fall back to owner (`whatsapp_service.py:134-138`).
- **Webhook unauthenticated** (H2); verify token weak default + `==` compare; outbound reply triggered by unauthenticated POST.

### Teams / MS 365 — FAIL (C1)
- Global device flow, global status, owner-stamped events, no per-user authorization on any route.

### n8n mailing workflows — FAIL single-user
- 3 workflows: `ai-chief-email-sync.json` (active, 10-min schedule) + 2 variants. All funnel to the shared `/api/integrations/emails/ingest` with a fixed Gmail credential; no user id, no per-user credential injection, no retry config.
- No WhatsApp reader workflows despite WhatsApp provider options.
- Shipped `.n8n.env` + README document a default `aichief` password.

---

## 7. AI ISOLATION TRACE (verified end-to-end)

| Hop | What carries identity | Verdict |
|---|---|---|
| enqueue | `enqueue_email(email_id)` — email belongs to exactly one user | ✅ |
| worker | `email_processor.process_email` snapshots `email.user_id` (`email_processor.py:105,270`) | ✅ |
| schedule/dedup | `_event_exists/_exam_exists/_assignment_exists` are scoped by `user_id` (`:55-86`) | ✅ |
| created rows | Exam/Assignment/Event stamped `user_id=user_id` (`:200,218,234`) | ✅ |
| prompt payload | only that email's subject/body sent to ollama; briefing service receives per-user data; news = global RSS (not user data) | ✅ |
| model host | local Ollama (`host.docker.internal:11434`) — no external AI calls | ✅ |

**Conclusion: AI context isolation is sound by construction** — identity travels `email_id → email row → user_id → outputs`. No cross-user mixing found. Queue is single-dimension (email ids), which is fine because the email row is the identity carrier.

---

## 8. BACKGROUND WORKER AUDIT

| Worker | Scope | Verdict |
|---|---|---|
| AI worker pool (`ai_processor.start()` at `main.py:205-213`) | bounded queue by email_id | ✅ isolation fine |
| Legacy startup probes (`main.py:215-223`) | global token.pickle + owner state | ⚠️ owner-only, legacy-only |
| Legacy token import → owner row (`main.py:224-238`) | idempotent | ✅ |
| `generate_reminders(force=True)` at startup (`main.py:240-242`) | scans all users; stamps each notification with the entity's `user_id` (`reminder_service.py:54-...`) | ✅ (dedup caveat M4) |
| Owner-only briefing pre-gen (`main.py:251-275`) | first user only | ⚠️ owner-only (per-user generation exists via API) |
| Session worker loop (`main.py:281-419`) | gmail + classroom per-user passes ✅; retention purge global (row-based, fine) | ✅ |
| `push_pending_notifications()` (`main.py:398-399`) | **global delivery** | ❌ (H1) |

---

## 9. SECURITY CONTROLS (verified)

- **Password hashing**: PBKDF2 (`security.py`); no plaintext storage.
- **Auth tokens**: HMAC-signed with `compare_digest` — not JWT, not leaked in `sub` (tokens carry `sub` = user id).
- **Legacy no-sub token resolution** → earliest/owner user (`deps.py:26-45`).
- **Per-route authorization**: `get_current_user` on 15/16 routers; `require_permission` wired for integration management (`user_integrations.py`); owner checks inline for user management (`auth.py manage_users`). **Gap: teams (C1)**.
- **Rate limiting / lockout** on login: 5 attempts → 60s, then 300s per IP (`auth.py:48-64`).
- **OAuth**: per-user state nonce + `compare_digest`; tokens encrypted (Fernet) at rest with `SECRET_KEY`.
- **Integration API key**: `verify_integration_key` → `hmac.compare_digest`; ingest off unless configured.
- **CORS**: fixed origin list (`main.py:171-177`, `allow_credentials=False`).
- **Fail-open auth** (L3), **public docs schema** (L2), **weak SECRET_KEY fallback** (L1), **unauthenticated WhatsApp webhook** (H2) are the notable gaps.

---

## 10. ACTUAL TEST RESULTS (executed during this audit)

Command (inside the docker backend container, host Postgres 16, test DB `ai_chief_test`):

```
cd /app && . venv/bin/activate && python -m pytest tests/ -q
```

Result:

```
33 passed, 20 warnings in 7.45s
```

Suites: `test_isolation.py` (cross-user A-vs-B matrix), `test_per_user_sync.py` (gmail/classroom/ingest/email_processor/reminder ownership), `test_auth.py`, `test_integrations.py`. The 20 warnings are `datetime.utcnow()` DeprecationWarnings (L4).

---

## 11. ACHIEVED REQUIREMENTS (summary)

1. Complete per-user data model with ownership columns on all user-facing tables (R1) and matching migrations 0001–0006 (R30).
2. Strong per-user Google OAuth: state nonce, encrypted tokens, per-user refresh, least-privilege scopes (R8-R10).
3. Per-user sync loops for Gmail and Classroom with isolated failures per user (R12-R13).
4. Per-user CRUD and stats across emails, events, exams, assignments, tasks, timetable, attendance, documents, briefings, dashboard (R15-R17, R20).
5. Notifications DB layer fully per-user; reminders owned by the entity's user (R18).
6. AI pipeline identity preservation verified end-to-end; local Ollama only (R24-R25).
7. Server-to-server ingest protected by a constant-time-compared, default-off API key (R28-R29).
8. Isolation test suite exercising cross-user scenarios; 33/33 passing (R31).
9. Frontend Settings per-user Google connect/disconnect/health/re-auth-replacement UI wired end-to-end (R32).
10. Login security: PBKDF2, HMAC tokens, rate-limit + lockout, roles/permissions (R3-R6).

## 12. MISSING / AT-RISK REQUIREMENTS

1. Teams per-user isolation — no authorization on any Teams route (R21, C1).
2. Push delivery isolation — subscriptions and notifications not joined per user (R19, H1).
3. n8n multi-user ingestion — fixed single credential, owner-stamped ingest (R26, H3, M5).
4. WhatsApp webhook authenticity — signatures not verified, weak token default (R23, H2).
5. Per-user email ingest — `email_ingest.py` owner-stamps all (R14).
6. Global cache isolation — account-email cache not user-keyed (R27, M1).
7. Emergency-code account binding — global master access to owner (M3).
8. Global-unique keys on notification fingerprints / message ids (M4).
9. Secret defaults / fallbacks — verify token, SECRET_KEY fallback, dev DB password, real-looking send-to number (§4).
10. Public docs schema (L2) and auth fail-open default (L3).

## 13. PRIORITIZED NEXT STEPS

**P0 (ship-blockers):**
1. Add `get_current_user` to all 4 Teams routes and move MS 365 tokens into `user_integrations` (per-user) — closes C1.
2. Make `push_pending_notifications` user-aware: deliver notification `n` only to subscriptions with `user.subscription.user_id == n.user_id` — closes H1.
3. Verify Twilio/Meta webhook signatures before processing (constants-time compare), and require a strong `META_WA_VERIFY_TOKEN` for the GET verify endpoint — closes H2.

**P1:**
4. Kill the legacy global token path: remove `/api/emails/re-auth` desktop flow and `token.pickle` read paths; keep the owner import as a one-time migration.
5. Key the account-email cache by `user_id` (or drop the cache, serializing the call) — closes M1.
6. Bind emergency codes to the requesting owner context or remove the feature; add `user_id` FK.
7. Make `email_ingest` accept/derive a user id (validate against `user_integrations.account_id` ↔ `From`/API-key binding).

**P2:**
8. Redesign n8n flows to carry a user identity (per-user credential injection or per-user ingest key) + read-retry config; or document as single-user-only.
9. Scope global-unique fingerprints/message ids by `user_id` (UNIQUE constraint on `(user_id, fingerprint)`).
10. Harden bootstrap: fail-fast on default `SECRET_KEY`; remove `aichief`/real-number defaults from compose; store n8n password as env-only.
11. Gate `/docs` behind auth in non-dev; decide product posture on auth fail-open.
12. Replace naive-UTC datetimes with timezone-aware handling (clean the 20 warnings).

---

**NO CODE WAS MODIFIED.**