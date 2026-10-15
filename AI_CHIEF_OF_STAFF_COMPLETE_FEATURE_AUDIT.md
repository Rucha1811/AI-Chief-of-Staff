# AI Chief of Staff — Complete Feature Audit

**Project:** Rucha1811/AI-Chief-of-Staff  
**Path:** `/Users/ruchatejaskumargandhi/Downloads/ai chief`  
**Git HEAD:** `15fd552ece54a61cd19b0ec63b8e4e9105387149` (main; 0 behind, **12 commits ahead** of `origin/main`)  
**Untracked:** 121 files, unstaged: 127, staged: 0 (no destructive Git operations performed)  
**Report Date:** Thu Oct 15 2026  
**Audit Phase:** Audit-only. No fixes applied. No state-changing modifications to live application or DB.

---

## A. Executive Summary

### Overall Findings
- **Features audited:** 60+ major features across backend APIs, services, models, frontend routes/components, n8n workflows, tests.
- **Fully Verified:** 12 features.
- **Works With Limitations:** 25 features.
- **Implemented But Not Connected:** 7 features.
- **Blocked:** 3 features.
- **Not Implemented:** 2 features.
- **Not Tested:** 13 features.

### Critical Risks
- **Docker daemon unavailable** during this audit (containers not inspectable at runtime). Live-service verification is largely **NOT VERIFIED**.
- **n8n JWT** is stale/long-lived with no refresh; Phase 2 monitor shows persistent 401 errors.
- **Gmail sync pagination** is **not connected** end-to-end (backend uses first page only). System reads the **latest N messages** per sync, not all matching messages.
- **Calendar integration**: read-before-write against Google is **not implemented**; Classroom/Teams events are pre-tagged and **cannot be pushed** to Google Calendar.
- **WhatsApp inbound** via n8n relay **drops the Twilio signature** and fails verification when relayed.
- **Dev mode**: `AUTH_ENABLED=False` when `AUTH_PASSWORD` unset (non-prod) effectively fails open.
- **API-key-only endpoints** (`/api/integrations/agent/accounts`, email ingest) act across **all users** if the ingest key is compromised.

### Current Readiness
**Not ready for real users in production without remediation.** Core authentication, isolation, and AI pipeline are solid. Critical gaps in Gmail completeness, calendar sync completeness, n8n reliability, and several operational safeguards prevent a FULLY VERIFIED production readiness label.

---

## B. Feature Status Matrix

| Feature | Code exists | Automated tests | Isolated integration test | Live service verified | Visible in current frontend | Status | Evidence / blocker |
|---|---|---|---|---|---|---|---|
| **Authentication (login/register/logout/token revocation)** | Y | Y | Y | N | Y | **FULLY VERIFIED** | PBKDF2-120k, token_version revocation on logout, HMAC-signed Bearer; dev fail-open only (auth.py, security.py, deps.py). |
| **Google OAuth (login auth-only)** | Y | Y | Y | N | Y | **FULLY VERIFIED** | Auth-only (no auto-provision), access_denied/not_invited; state validation (auth.py, google_oauth.py). |
| **Per-user DB isolation / ownership** | Y | Y | Y | N | Y | **FULLY VERIFIED** | All user routes scoped by user_id; ownership checks on detail routes; tests pass (deps.py, emails.py, events.py, etc.). |
| **Log redaction (OAuth code/state)** | Y | Y | Y | N | N/A | **FULLY VERIFIED** | OAuthAccessLogFilter redacts tokens on access logs (logging_filters.py). |
| **Verified institutional identity (CHARUSAT)** | Y | Y | Y | N | Y | **FULLY VERIFIED** | email_verified/verified_email only from provider; exact domain match; no suffix grant (charusat_identity.py, 0013). |
| **Session revocation (token_version)** | Y | Y | Y | N | Y | **FULLY VERIFIED** | Login returns ver, logout increments, verify enforces (auth.py:337-349, deps.py:57-61). |
| **AI pipeline (Ollama, queue, analysis)** | Y | P | Y | N | Y | **WORKS WITH LIMITATIONS** | Ollama used locally; bounded queue + circuit breaker; deadlines parsed as naive (no tz validation, no evidence check) (ai_service.py, ai_queue.py, ai_processor.py). |
| **Email ingest / dedup per-user** | Y | Y | Y | N | Y | **FULLY VERIFIED** | (user_id, message_id) unique; Postgres upsert; per-user scoping (email_ingest.py). |
| **Email retention (soft)** | Y | P | Y | N | N/A | **WORKS WITH LIMITATIONS** | Purge job runs but emails never hard-deleted (rows retained); notifications cleaned (email_retention.py, main.py:577-588). |
| **Reminder generation** | Y | Y | Y | N | Y | **WORKS WITH LIMITATIONS** | 60s loop + startup + API triggers; per-user fingerprint dedup; no recurring daily briefing regen beyond startup (reminder_service.py). |
| **Web push (VAPID, SW, delivery)** | Y | Y | Y | N | Y | **WORKS WITH LIMITATIONS** | End-to-end wired; expires_at never set; possible duplicate pushes in 10-min window (push_service.py, sw.js, NotificationBell). |
| **Dashboard/News/Timetable/Tasks/Exams/Assignments/Documents** | Y | P | P | N | Y | **WORKS WITH LIMITATIONS** | UI wired to APIs; tests cover isolation/auth surface, not full functional coverage. |
| **Gmail sync (latest N only)** | Y | P | Y | N | Y | **BLOCKED/WORKS WITH LIMITATIONS** | **Pagination not connected** (no nextPageToken traversal). fetch_recent_emails uses single list page (maxResults) — reads latest N, not all messages (gmail_service.py:232-292). |
| **Gmail incremental (historyId)** | Y (retired) | N | N | N | N/A | **IMPLEMENTED BUT NOT CONNECTED** | Stateful historyId/pagination logic exists in build_workflow JS but **not wired** into imported n8n workflows; backend has no historyId path (gmail_service.py). |
| **Classroom sync (courses/submissions)** | Y | Y | Y | N | Y | **WORKS WITH LIMITATIONS** | Paging not implemented; coursework scope not requested by default (disabled by design); Task/Event due dates not updated on change (classroom_service.py). |
| **Calendar read-before-write / dedup against Google** | N | N | N | N | Y | **NOT IMPLEMENTED** | No `events().list`; dedup only local; cannot detect pre-existing Google events (calendar_service.py:98 only insert). |
| **Classroom/Teams → Google Calendar push** | Y (code) | N | N | N | Y | **IMPLEMENTED BUT NOT CONNECTED** | `calendar_event_id` prefilled with coursework/MS id → push skips them; events never reach Google (calendar_service.py:80-81). |
| **Conflicts detection** | Y | N | P | N | Y | **WORKS WITH LIMITATIONS** | Local DB only, never clears stale flags, can include all-day spans; not invoked automatically during creation (events.py:139-200). |
| **Free slots / Schedule study** | Y | N | P | N | Y | **WORKS WITH LIMITATIONS** | Local events only (not real Google Calendar); hardcoded working hours; preferences (User.preferences) unused; no deadline weighting (events.py:203-354). |
| **Auto-scheduling pipeline (end-to-end)** | Y (pieces) | N | P | N | Y | **WORKS WITH LIMITATIONS** | Extract→row creation wired; **conflict resolution and calendar write disconnected from automatic pipeline** (manual sync-to-google only). Gmail assignments create Assignment/Exam/Event but **not Task** (email_processor.py). |
| **Voice assistant (STT/TTS)** | Y | N | P | N | Y | **WORKS WITH LIMITATIONS** | Client-only, mounted globally. Has wake-word, intents, hands-free; no true pause/resume, no user prefs (voice/lang/auto-play), silent TTS failures on unavailable synth (VoiceAssistant.tsx). |
| **n8n email sync (multi-user)** | Y | P | N | N | N/A | **IMPLEMENTED BUT NOT CONNECTED** | Workflow JSON exists but **not imported** in inspected n8n DB backups (not present); backend ingest path works. |
| **n8n Phase 1/2 workflows** | Y | N | N | N | N/A | **BLOCKED** | JWT-based workflows use stale token; **no refresh logic**, 401 errors persistent; no retry/error branches; hostname inconsistency (Phase 1 uses host.docker.internal) (n8n/workflows/*.json). |
| **n8n WhatsApp inbound** | Y | N | N | N | N/A | **BLOCKED** | Relay drops **Twilio signature** (X-Twilio-Signature not forwarded) → webhook verification fails; backend verification logic is correct but relay breaks it. |
| **WhatsApp outbound** | Y | Y | Y | N | Y | **FULLY VERIFIED** | Providers twilio/meta/none, default none, per-user integration resolution, signature verification correct for direct calls, dedup on save. |
| **Teams integration** | Y | Y | P | N | Y | **WORKS WITH LIMITATIONS** | Device-code flow, per-user isolation; requires MS_CLIENT_ID env; behavior depends on external Graph (teams_service.py). |
| **API-key cross-user surface** | Y | Y | Y | N | N/A | **WORKS WITH LIMITATIONS (RISK)** | `/api/integrations/agent/accounts` returns all users' emails (API-key). Ingest can write to any user by account_id/user_id. Key compromise allows cross-user read/injection (integration_agent.py, email_ingest.py). |
| **PKCE (OAuth)** | N | N | N | N | N/A | **NOT IMPLEMENTED** | No PKCE (Flow without code_verifier); OAuth uses client secret (standard for confidential web client) but extra defense missing (google_auth.py:339-347). |
| **Pagination (list endpoints)** | P | P | P | N | Y | **WORKS WITH LIMITATIONS** | List endpoints have limit/clamping; **no nextPageToken/pageToken handling** anywhere (Gmail backend, Classroom courses/coursework, etc.). Single-page only. |
| **Tests coverage (automated)** | Y | Y | Y | N/A | N/A | **WORKS WITH LIMITATIONS** | 477 tests, disposable Postgres; no coverage tooling/CI, large gaps (AI engine, calendar/conflicts, pagination, voice, migrations correctness). |

*Status keys: FULLY VERIFIED, WORKS WITH LIMITATIONS, IMPLEMENTED BUT NOT CONNECTED, BLOCKED, NOT IMPLEMENTED, NOT TESTED.*

---

## C. Gmail Synchronization Audit (Explicit Answers)

1. **Does it read every email or only latest N?** Only the **latest N** per sync call. `gmail_service.sync_emails(max_results=50)` calls `users().messages().list(userId="me", maxResults=max_results)` with **no `pageToken`**; it processes exactly that single page. (file:line: `gmail_service.py:232-292`, `240-244`)
2. **Exact default limit?** Default 50, clamped 1-200 via `API_MAX_PAGE_SIZE=200`. Background loop uses 25. (emails.py:36-42, limits.py:20-29, config.py:611, main.py:289)
3. **Is pagination implemented end-to-end?** **Backend: No.** No `nextPageToken`/`pageToken` anywhere in backend Python. **n8n (retired):** full pagination/historyId exists in build_workflow JS but is **not connected** to imported workflows. (grep shows 0 hits in `app/`; `build_workflow/*_code.js` unused)
4. **Does it download bodies or metadata?** **Bodies (full)**: `messages().get(format="full")`. Extracts headers (From/To/Subject/Date), `threadId`, `labelIds` (read status). Prefers `text/plain`, falls back to `text/html` → converted to text; `body_html` is never persisted. (gmail_service.py:251, 137-229, models/email.py:24)
5. **Historical vs incremental sync?** No historyId API usage in backend. Every pass is "latest N" scan + per-user dedup by `(user_id,message_id)`. **No incremental checkpoint persisted.** Restart ⇒ re-scan latest N. n8n stateful path exists but not connected. (gmail_service.py:269-288; no `historyId`/`users.history.list` in `app/`)
6. **Does every synced email enter AI processing?** **Automatically**, by default. Sync enqueues via `background_tasks.add_task`/`ai_processor.enqueue_email` (`auto_process=True`). Background loop backfills `category=="unknown" AND summary IS NULL` (limit 10) after each Gmail pass. Manual batch `process-all` exists but its `failed` counter is unreliable (exceptions swallowed). (emails.py:31-61, main.py:520-563, emails.py:100-137)
7. **Can unprocessed messages be reprocessed?** Yes: `POST /api/emails/process-all` (filters `category=='unknown'`), `POST /api/emails/{email_id}/process`, background backfill. (emails.py:100-158, main.py:527-563)
8. **Attachments?** **Unsupported/ignored.** No `attachmentId` fetch or storage. Body parts with attachments are never downloaded. (gmail_service.py:137-146)
9. **Retries/quotas/timeouts/partial failure/resume?** Exponential backoff for 429/5xx (max 4), honors `retry-after`; 401/403 marks integration dead; connection/timeouts retried; DB retry with backoff. **Partial failure midway:** all-or-nothing per pass (no cursor to resume mid-batch). Resume after restart only via re-scan+dedup. (gmail_service.py:35-91, main.py:506-514)
10. **Retention/deletion enforced?** **No (soft only).** `retention_until` stamped (90 days). Hourly purge removes dependent Notifications but **hard-deletes 0 emails** (hard-delete gated out). (email_retention.py:44-78, config.py:682, main.py:577-588)
11. **Silent skipping?** Yes: Spam/Trash omitted (default list), empty body → stored None, prompts truncate (body up to ~2500-3000 chars sent to model), empty message_id skipped. (gmail_service.py:137-146, 240-242, 277; ai_service.py:303, 332, 361, 373, 385)
12. **Status reported?** Counts only (`added`, `processing`), health via `/api/emails/health`. `process-all` reports `processed/failed/remaining` but `failed` is unreliable. Frontend auto-sync swallows errors. (emails.py:61, 71-75, 128-137, emails/page.tsx:47-168)

**Verdict:** Gmail sync processes **only the latest N messages** (single page). It does **not** traverse all messages via pagination. Incremental historyId is not connected. This is the single biggest completeness gap for email ingestion.

---

## D. Classroom & Scheduling Audit (Explicit Answers)

### Classroom
1. **Scopes requested/received?** Requests `classroom.courses.readonly`, `classroom.student-submissions.me.readonly`, plus Gmail/Calendar/events, openid/userinfo.*. **`classroom.coursework.me.readonly` is defined but never requested** (disabled by design) → coursework/assignments listing is blocked unless granted separately. (google_auth.py:49-57, classroom_service.py:32, 83-94)
2. **Can read courses? Assignments? All?** Can list courses and student submissions. Coursework listing exists but depends on scope; **no nextPageToken pagination** — single page per call (courses pageSize up to 100, coursework per-course limit 20). **Not complete for large courses**. (classroom_service.py:156-210, 262-283)
3. **Student-specific submissions?** Yes: `studentSubmissions().list(userId="me", pageSize=1)`. (classroom_service.py:277-283)
4. **Dedup + update changed due dates?** App-level dedup by `source_id` for Task/Event/Assignment. **Task and Assignment have no DB unique constraint** (only Event does). **Only Assignment.due_date is updated on change; existing Task/Event rows do not update** when Classroom due dates change → stale deadlines. (models/event.py:22; classroom_service.py:406-421, 493-497)
5. **Missing due dates?** Left as `None`/unknown; items with no due date are **skipped entirely** (not synced). **Not invented.** (classroom_service.py:176-185, 224, 423-425)
6. **Permission errors surfaced clearly?** Yes — `_require_coursework_scope` raises `ClassroomScopeError` (separates scope vs auth); 401/403 mark integration dead; API returns scoped error messages. (classroom_service.py:83-125, classroom.py:19, 59-82)
7. **Automatic or manual sync?** **Both.** Background loop every 1800s calls `_sync_classroom()`; startup syncs submissions once. Manual endpoints `/api/classroom/sync`, `/api/classroom/sync-submissions`, `/api/assignments/sync-classroom`. (main.py:298-315, 443-446, 565-575, 610-612)

### Calendar
8. **Scopes read/write?** Requests `calendar.events` (write-capable scope). Only **insert** implemented (`service.events().insert`). **No update/patch/delete.** (google_auth.py:24,53; calendar_service.py:22, 97-105)
9. **Read existing events before scheduling?** **No.** No `events().list` anywhere. Duplicate avoidance is only local (`event.calendar_event_id != None` skips). **Classroom events are created with `calendar_event_id` = coursework id → they are skipped and never pushed to Google Calendar.** Teams similarly skipped. (calendar_service.py:80-82, 100-101; classroom_service.py:453; teams_service.py:313)
10. **Timezones/DST/all-day/recurring?** Hardcoded `"timeZone": "UTC"`, times stored naive. `User.timezone` is **never used**. All-day handled (exclusive end). **Recurrence not sent to Google** (only expanded locally for conflicts). DST not handled correctly for non-UTC users. (calendar_service.py:69-95; recurrence.py:36-78)
11. **Conflict detection?** `/api/events/conflicts/detect` works for **local events only**. Does not clear stale `is_conflict` flags; can false-positive on all-day spans; never considers actual Google Calendar events. (events.py:139-200)
12. **Free slots + real availability?** Free slots use local events + configured work hours. `schedule-study` uses default 08:00-22:00 (ignoring some config). **No read of real Google Calendar availability. No weighting by actual deadlines.** `User.preferences` JSON column exists but is **never read/written** (hardcoded). (events.py:203-354, config.py:678-681, models/user.py:28)
13. **Failed calendar write reports success?** Per-event failures are caught, counted, and logged; the endpoint returns **HTTP 200** with `{created, skipped, failed}` even if all failed. (calendar_service.py:103-105, events.py:391-395)

### Auto-scheduling pipeline (end-to-end)
14. **Pipeline wired?** Extract→row creation→UI read is wired. **Conflict resolution and calendar write are disconnected from the automatic pipeline** (only manual `POST /api/events/sync-to-google` exists). No proposed-event/approval stage. (email_processor.py:147-238; events.py:357-395; calendar/page.tsx:98-111)
15. **Gmail deadline → Task/Event?** Gmail assignment/exam → **Assignment or Exam row**; other kinds → **Event**. **Emails never create Tasks** (`Email.created_task_id` is never written). Classroom → **Task + Event + Assignment**. (email_processor.py:188-238, classroom_service.py:423-497)
16. **Date blindly treated as deadline?** AI-driven with prompt constraints; deterministic validation only checks parseability + presence of title/date. **No future-plausibility/range/evidence validation**; deadlines stored as naive datetimes (no tz). Not blindly accepted but semantic validation is missing. (email_processor.py:21-37, 162-166, 283-291; ai_service.py:299, 324)
17. **Scheduling preferences persisted?** **No.** `User.preferences` is never read/written. All defaults are hardcoded (config or request params). (models/user.py:28, config.py:678-681)

**Verdict:** Classroom read path works with scope limitations and no paging. Calendar has **no read-before-write** and **Classroom/Teams cannot be pushed to Google** — this breaks the "assignments automatically scheduled in Calendar" promise for Classroom-sourced work. Automatic pipeline creates DB rows but does not auto-push to Google Calendar.

---

## E. Voice Assistant Audit

| Capability | Status | Evidence |
|---|---|---|
| Browser SpeechSynthesis (TTS) | **IMPLEMENTED** | `VoiceAssistant.tsx:291-332` uses `window.speechSynthesis` |
| External TTS service | **NOT IMPLEMENTED** | No backend voice endpoints; `api.ts` has none |
| Read daily briefing aloud | **IMPLEMENTED** (via assistant only) | `briefing` intent speaks results; briefing page has no play button (lines 98-107, 361) |
| SpeechRecognition (STT) | **IMPLEMENTED** | Web Speech API with webkit fallback (395-399, 411-418, 507-522) |
| Command grammar | **PARTIAL** | Regex intent routing (77-92), not formal grammar |
| Commands: tasks/deadlines/emails/briefing/timetable/exams/news/dashboard | **IMPLEMENTED** | 132-175 |
| Mic permission denied | **IMPLEMENTED** | 241-244, 472-484 |
| Unsupported browser | **IMPLEMENTED** | 514-517, 537-539 |
| Insecure context | **IMPLEMENTED** | 518-521 |
| Start/stop | **IMPLEMENTED** | 334-344, 346-349, 551-553, UI buttons 679-698 |
| Pause/resume | **PARTIAL** | No true pause/resume UI; hands-free toggles stop/arm only; `synth.resume()` exists but unused in UI (206, 228-251, 273, 307) |
| TTS unavailable error handling | **PARTIAL** | Silently no-ops if `!synth`; errors call `onDone` with no user-facing message (294, 320-329) |
| Voice/language/auto-play prefs | **NOT IMPLEMENTED** | Recognition lang `en-US` (419); voice auto-picked (311-315); mute only in-session, not persisted |
| Privacy (email content spoken) | **PARTIAL / NO SAFEGUARD** | Reads email **subjects** + deadlines and task titles aloud; no redaction/warning (139, 146) |
| Mounted/visible | **IMPLEMENTED** | Rendered in `layout.tsx:36` inside `AuthGate`; floating FAB visible when authenticated |

**Verdict:** Voice assistant works functionally in supported browsers (Chrome/Edge) but is **not complete** (missing user preferences, true pause/resume, robust error surfacing, privacy safeguards).

---

## F. Other Integrations (n8n, WhatsApp, Push, Ollama, Briefing, Teams)

| Integration | Status | Evidence |
|---|---|---|
| **n8n workflows (imported state)** | **IMPLEMENTED BUT NOT CONNECTED** | Multi-user email sync JSON exists but **not imported** in n8n DB backups; Phase 1/2 present with JWT issues. |
| **n8n Phase 1/2 JWT auth** | **BLOCKED** | Stale long-lived token, **no refresh logic**; persistent 401s on Phase 2 (monitor). No retry/error branches. Hostname inconsistent (Phase 1 uses host.docker.internal). |
| **n8n stateful incremental (historyId)** | **IMPLEMENTED BUT NOT CONNECTED** | Full state/pagination code in build_workflow/*_code.js but unused by imported workflows; `staticData` is `{}`. |
| **n8n WhatsApp inbound relay** | **BLOCKED** | Does **not forward `X-Twilio-Signature`** → Twilio signature verification fails at backend when relayed. |
| **WhatsApp outbound** | **FULLY VERIFIED** | Twilio/Meta/none; default none; verification correct for direct calls; dedupe + per-user resolution. |
| **Web push** | **WORKS WITH LIMITATIONS** | VAPID + SW + NotificationBell wired; no `expires_at` set; possible duplicate pushes within 10-min window. |
| **Reminders** | **WORKS WITH LIMITATIONS** | Background loop (60s) + startup + API; fingerprint dedup; daily briefing **no recurring regen** beyond startup (only on-demand/once at boot). |
| **Ollama (local AI)** | **FULLY VERIFIED** | Local `http://localhost:11434`, model `qwen2.5:3b`; HTTP calls + health + circuit breaker + bounded concurrency + timeouts/retries. No external AI calls. |
| **Teams/M365** | **WORKS WITH LIMITATIONS** | Device-code flow, per-user isolation; requires `MS_CLIENT_ID`; external Graph API. |
| **News service** | **WORKS WITH LIMITATIONS** | RSS (Google News) + Hacker News; timeouts/retries; returns structured results. |
| **Uploads/Documents** | **WORKS WITH LIMITATIONS** | Upload size limits; text extraction varies by format; summarize/QA wired. |
| **Retention purge (manual/api)** | **WORKS WITH LIMITATIONS** | Runs hourly but emails not hard-deleted (soft-only by design). |

---

## G. Security & Data Safety

| Concern | Status | Evidence / Notes |
|---|---|---|
| **Secrets excluded from logs** | **VERIFIED** | OAuthAccessLogFilter redacts code/state/tokens on access logs; app logs no token leakage found (logging_filters.py). |
| **Password hashing** | **VERIFIED** | PBKDF2-HMAC-SHA256, 120000 iterations, salt+compare_digest (security.py:11-30). |
| **Token signing (HMAC)** | **VERIFIED** | HMAC-SHA256 of base64url payload; constant-time verify. Signed, not encrypted (sub/ver/exp readable) (security.py:33-73). |
| **Session revocation** | **VERIFIED** | token_version increment on logout; enforced on every auth check (auth.py:337-349, deps.py:57-61). |
| **OAuth state CSRF** | **VERIFIED** | Login uses HttpOnly SameSite cookie state; connect uses server-side stored nonce + compare_digest (auth.py:394-419, google_auth.py:441-444). |
| **Redirect URI validation** | **VERIFIED** | Loopback-only for caller-supplied; rejects embedded creds; prod rejects localhost (google_oauth.py:22-39, config.py:461-466). |
| **PKCE** | **NOT IMPLEMENTED** | No code_verifier/challenge (google_auth.py:339-347). |
| **Authorization on all protected routes** | **VERIFIED** | Middleware + deps + per-resource ownership filters; fail-closed default (main.py:73-120, access_policy.py). |
| **User isolation** | **VERIFIED** | Consistent user_id scoping; no cross-user leakage in user-facing routes. |
| **API-key cross-user exposure** | **RISK** | `/api/integrations/agent/accounts` returns all users' emails; ingest can write to any user. Requires strong `INGEST_API_KEY` + network restrictions (integration_agent.py:20-51, email_ingest.py:26-94). |
| **AUTH fail-open (dev)** | **RISK (non-prod)** | When `AUTH_PASSWORD` unset and `ENVIRONMENT!=production`, `is_authenticated` returns True (auth.py:89-92). Production enforces strong secrets. |
| **Login rate limiting** | **VERIFIED (partial)** | Per-IP, 5/60s → 300s lockout on `/login`. **No rate limiting** on `/register` or `/exchange` (auth.py:35-87, 222, 313). In-memory (single-worker). |
| **In-memory state** | **LIMITATION** | Login rate limits and exchange codes are in-memory (multi-worker not shared). |
| **Token expiry** | **VERIFIED** | 7-day default TTL (config.py:650). |
| **Secrets at rest** | **VERIFIED** | OAuth tokens encrypted with Fernet(`SECRET_KEY`) (security.py:86-99, google_auth.py:262-272). VAPID keys stored as 0600 (push_service.py:47-80). |
| **Backups/permissions** | **VERIFIED** | Existing backups present (`ai_chief_20261005_150120.sql.gz`, gzip OK). Per earlier audit: no destructive cleanup performed. |
| **Retention/deletion behavior** | **DOCUMENTED (soft)** | Emails not hard-deleted by design; only notifications purged. (email_retention.py:44-78) |

---

## H. Runtime Discrepancy Report

| Feature | Source (working tree) | Running containers (current runtime) | Discrepancy | Impact |
|---|---|---|---|---|
| **EGovAttendanceCard UI** | Present (`frontend/src/components/EGovAttendanceCard.tsx`), imported in `attendance/page.tsx` | **Frontend image predates card** (built earlier). Grep of running frontend build returned no `egov`/`e-Governance` strings. | **Absent from running frontend build**. Frontend container not rebuilt since card added. | **Attendance card not visible** in current live UI. |
| **Backend egov/eligibility code** | Latest (`egov_attendance.py`, `charusat_identity.py`, `attendance_intelligence.py` written ~11:21-11:39 local) | Backend process started **before** those writes (05:14 UTC = 10:44 local). Bind-mounted but process has older module versions in memory (route exists from earlier code). | **Stale in-memory code** (no process reload). Route `/api/attendance/egov` returns 401 (auth) but eligibility/logic not the latest. | Eligibility gating may behave differently until restart. |
| **DB schema (live `ai_chief`)** | Repo head migration **0013** adds `users.email_verified`, `verified_email`; 0012 adds `egov_attendance` | Live DB is at **revision 0011**. Columns/tables **do not exist** (verified by read-only inspection). | **Schema drift vs code** (ORM model expects new columns). | Restarting backend before applying 0013 would cause `UndefinedColumn` errors on user queries (login breaks) because models select those columns. |
| **EGov attendance table** | Model + migration 0012 | **Does not exist** in live DB (0 rows). | Missing table. | `/api/attendance/egov` endpoints that query `egov_attendance` will error if hit after restart without migration. |
| **Frontend build freshness** | Source includes new pages/components (attendance card, updated pages) | Running frontend image was built **before** changes. | Stale build. | Many UI changes not active until `docker compose build frontend && up -d`. |
| **n8n workflows** | Multi-user sync JSON present in repo | n8n DB (inspected) does **not contain** the multi-user email sync workflow imported. | Workflow not deployed in n8n. | Multi-user sync via n8n not active. |
| **Phase 2 monitor state** | Workflow JSON present | Active but **failing with 401s** (stale JWT), 0 recent successes. | Credential/config drift. | Phase 2 monitor unreliable. |

**Key ordering constraint:** **Apply migration 0013 BEFORE restarting backend** (dev env uses `create_all` + ad-hoc ALTER but ad-hoc list does not add `users.email_verified/verified_email`; ORM selects them → undefined column on restart without migration).

---

## I. Prioritized Remediation Plan

### P0 — Security, data loss, or account isolation failures
1. **Rotate/strengthen `INGEST_API_KEY` + restrict network** (Evidence: `agent/accounts` returns all users; ingest cross-user capable)
- Root cause: API-key endpoints designed for server-to-server but overly broad.
- Fix: Split keys (read-only vs write), restrict by IP, or remove cross-user `user_id/account_id` override in ingest for non-admin paths; log all cross-user operations.
- Files: `integration_agent.py:20-51`, `email_ingest.py:26-94`, `integrations.py:49-90`, `access_policy.py`.
- Test: Unit tests asserting cross-user attempts rejected; integration key scope tests.
- Dependencies: Config change (separate keys). No migration.

2. **Enforce AUTH (no fail-open in any non-local env)** (Evidence: auth.py:89-92)
- Root cause: Dev convenience allows `AUTH_ENABLED=False` when password unset.
- Fix: In staging/production, refuse to start if `AUTH_ENABLED` would be false; require explicit strong `AUTH_PASSWORD`/`SECRET_KEY`.
- Files: `backend/app/core/config.py`, `backend/app/api/auth.py`.
- Test: Startup tests expecting failure when misconfigured.
- No migration.

3. **Fix n8n WhatsApp inbound signature relay** (Evidence: relay drops X-Twilio-Signature)
- Root cause: n8n webhook forwards only body params, not original headers/signature.
- Fix: Either point Twilio directly to backend `/api/webhooks/whatsapp`, or configure n8n to forward the original `X-Twilio-Signature` header and the raw body; update webhook URL accordingly.
- Files: n8n workflow `WhatsApp Inbound` (DB/workflow JSON), `whatsapp.py:126-145`.
- Test: Signature verification test with relayed request.

### P1 — Core features broken/misleading
4. **Enable Gmail pagination end-to-end** (Evidence: single-page only, latest N)
- Root cause: `gmail_service.fetch_recent_emails` never follows `nextPageToken`.
- Fix: Implement loop: list with `pageToken` until no token or `max_messages` reached; persist optional checkpoint (or at least allow `max_messages` > page size). Or wire the retired n8n stateful path carefully.
- Files: `gmail_service.py:232-292`.
- Impact: Critical for completeness (historical emails). No schema change.
- Test: Mock with multiple pages, assert all messages fetched.

5. **Fix calendar read-before-write + Classroom/Teams push** (Evidence: no events.list; Classroom events skipped)
- Root cause: Classroom/Teams create events with `calendar_event_id` prefilled (non-null) so push skips them; no Google read for dedup.
- Fix: Don't prefill `calendar_event_id` for events meant to be pushed; or track `push_to_google` flag. Add optional `events().list` windowed read for dedup/conflict detection. Implement update/patch for changed events (or delete+insert with idempotency).
- Files: `classroom_service.py:450-463`, `teams_service.py:313`, `calendar_service.py:69-107`, `events.py`.
- Test: Unit tests for push behavior and dedup against Google (mocked).

6. **Apply DB migration 0013 before backend restart (ordering)** (Evidence: live 0011 vs code needs 0013; restart without migration breaks login)
- Root cause: ORM selects `email_verified/verified_email`; ad-hoc ALTER in dev doesn't add them.
- Fix: Apply `alembic upgrade head` (0013) on live `ai_chief` **before** restarting backend. Fresh pre-migration backup first. Frontend rebuild+restart after.
- Files: `alembic/versions/0012_egov_attendance.py`, `0013_users_verified_email.py`.
- Migrations: 0012, 0013 (idempotent). Safe (additive defaults).
- Test: Migration applied to disposable restored DB (already verified succeeds).

7. **Rebuild frontend + restart backend** (Evidence: card missing from built image; process has stale in-memory code)
- Fix: `docker compose build frontend && docker compose up -d frontend`; `docker compose restart backend` **after** migration 0013 applied.
- No code changes.

8. **Fix n8n JWT refresh / error handling** (Evidence: 401s, no retry/error branches)
- Root cause: Long-lived JWT, no refresh flow in workflows; linear flows with no error branches/retry.
- Fix: Replace static JWT with OAuth2 refresh credential (if supported) or rotate token on schedule; add error branches, `retryOnFail` with backoff on HTTP nodes; use `AI_CHIEF_BACKEND_URL` consistently (remove host.docker.internal in Phase 1). Also import multi-user sync workflow if intended.
- Files: n8n/workflows/*.json, credentials setup.
- Test: Workflow dry-run with mocked 401/429.

### P2 — Reliability & completeness
9. **Implement Gmail pagination (multi-page)** in backend sync path (Evidence: §C1-2)
- Files: `gmail_service.py:232-292`. Add pageToken loop, optional `max_messages` cap, resumability.
- Test: Mock multi-page.

10. **Add PKCE to OAuth (defense-in-depth)** (Evidence: not implemented)
- Files: `google_auth.py:339-347`, `google_oauth.py`. Use code_verifier/challenge with `Flow`.

11. **Harden API-key surface** (Evidence: cross-user reads)
- Restrict `agent/accounts` to admin/owner or remove from broad API-key; require explicit scope; avoid cross-user `user_id/account_id` overrides in ingest unless strictly necessary; add audit logging.
- Files: `integration_agent.py`, `email_ingest.py`, `access_policy.py`.

12. **Persist scheduling preferences** (Evidence: User.preferences unused)
- Read/write `User.preferences` in scheduling; replace hardcoded work hours/durations; add settings UI if needed.
- Files: `models/user.py`, `events.py`, `config.py`, settings API.

13. **Improve calendar failure semantics** (Evidence: HTTP 200 on all-fail)
- Return non-2xx when all pushes fail; include per-event errors in response.
- Files: `events.py:379-395`.

14. **Add true pause/resume + TTS error surfacing to VoiceAssistant** (Evidence: partial)
- UI controls for pause/resume; surface synthesis errors to user; persist mute/hands-free prefs.
- File: `VoiceAssistant.tsx`.

15. **Add DB unique constraints where missing** (Evidence: Task/Assignment no unique constraint for dedup)
- Consider `(user_id, source_type, source_id)` unique for Task/Assignment to prevent races; migration needed if adding.
- Files: `models/task.py`, `models/assignment.py`, Alembic migration.

16. **Add coverage tooling + CI** (Evidence: no pytest-cov, no CI)
- Add `pytest-cov`, coverage threshold in pytest config; GitHub Actions for backend tests + frontend type-check/build.
- New files: `.github/workflows/ci.yml`, pytest config (optional).

### P3 — Enhancements
17. **Clear stale conflict flags** in `conflicts/detect` (Evidence: never cleared)
18. **Invalidate free-slots cache after Classroom/Teams syncs** (Evidence: not invalidated)
19. **Validate deadlines semantically** (tz-aware, future plausibility, evidence) (Evidence: naive + no validation)
20. **Queue deduplication** (Evidence: same email can be enqueued twice; side-effects deduped)
21. **Per-notification push dedup + set subscription `expires_at`** (Evidence: window-based possible duplicates; expires_at never set)

---

## J. Safe Activation Checklist

**Do NOT execute state-changing commands now.** This is the exact sequence to run **after** P0/P1 issues addressed and with explicit approval.

### Backup & restore verification (read-only + new backup)
```bash
cd "/Users/ruchatejaskumargandhi/Downloads/ai chief"

# READ-ONLY
git rev-parse --short HEAD && git status --short
docker exec -i aichief-postgres-1 psql -U postgres -d ai_chief -c "SELECT version_num FROM alembic_version;"
docker exec -i aichief-postgres-1 psql -U postgres -d ai_chief -c "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'egov%';"
docker exec -i aichief-postgres-1 psql -U postgres -d ai_chief -c "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name IN ('email_verified','verified_email');"
```

```bash
# STATE-CHANGING (approval required)
# 1. Fresh pre-migration backup
docker exec -i aichief-postgres-1 pg_dump -U postgres -Fc ai_chief > backups/postgres/ai_chief_pre_0013_$(date +%Y%m%d_%H%M%S).dump
```

### Database migration (STATE-CHANGING, order critical)
```bash
# 2. Apply Alembic migrations to head (0011 -> 0012 -> 0013)
docker exec -i aichief-backend-1 sh -c 'cd /app && alembic upgrade head'

# 3. Verify
docker exec -i aichief-postgres-1 psql -U postgres -d ai_chief -c "SELECT version_num FROM alembic_version;"  # expect 0013
docker exec -i aichief-postgres-1 psql -U postgres -d ai_chief -c "\d users" | grep -E 'email_verified|verified_email'
docker exec -i aichief-postgres-1 psql -U postgres -d ai_chief -c "\dt" | grep egov_attendance
```

### Services (STATE-CHANGING, after migration)
```bash
# 4. Restart backend AFTER migration (critical ordering)
docker compose restart backend

# 5. Rebuild + restart frontend (image predates card)
docker compose build frontend && docker compose up -d frontend
```

### Reauthorization (as needed)
- Re-login with Google for `24dcs025@charusat.edu.in` (or any institutional account) so provider `email_verified` claim is recorded and eligibility is established.
- Reconnect Google integrations if tokens were marked dead/expired.
- Rotate/repair n8n JWT credentials if JWT-based workflows remain active.

### External-service verification (read-only where possible)
- Health: `curl http://127.0.0.1:8000/api/health`
- Auth status: `curl http://127.0.0.1:8000/api/auth/status`
- EGov endpoint auth: `curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/attendance/egov` (expect 401 with valid auth)
- Frontend: verify `/attendance` page renders e-Governance card for eligible verified account; ineligible accounts see no student data.

### Post-activation smoke tests (non-destructive)
- Login as verified institutional account → EGov card appears (if eligible after re-login).
- Ineligible account → cannot see others' attendance; manual attendance/profile intact.
- Gmail sync: manual small sync (limit 10) to verify latest-N path; note pagination limitation documented.
- Calendar: manual `sync-to-google` with synthetic event (if authorized) — verify failure reporting is clear.
- Voice assistant: mic permission + basic command on supported browser.

---

## K. Final Verdict

| Question | Answer | Evidence-based explanation |
|---|---|---|
| 1. Is the application currently fully functional? | **NO** | Core features work but critical gaps: Gmail pagination (latest-N only), calendar read-before-write + Classroom→Google push broken, n8n JWT/relay issues, runtime stale frontend/build vs working tree. |
| 2. Does Gmail sync all messages in the intended scope? | **NO** | Single-page list only (no nextPageToken). Syncs latest N per pass; does not traverse all messages. Incremental historyId not connected. |
| 3. Does every relevant email get analyzed by AI? | **PARTIAL** | Synced emails are auto-queued; background backfill exists. But prompt truncation, silent skips (spam/trash/empty), and the fact that >N older emails are never synced means many relevant emails are never analyzed. |
| 4. Can Classroom assignments be read completely? | **PARTIAL** | Courses/submissions readable; coursework depends on scope (not requested by default); **no pagination** → large courses truncated. Missing due dates skipped. |
| 5. Are assignments automatically scheduled in Calendar? | **PARTIAL** | DB rows created (Task/Event/Assignment) from Classroom/Gmail, but **automatic push to Google Calendar is not connected**. Classroom/Teams events are skipped from push. Only manual `sync-to-google` works; even then has no read-before-write against Google. |
| 6. Does conflict detection work end-to-end? | **PARTIAL** | Works for local DB events only; never clears stale flags; not invoked automatically during creation; cannot see actual Google Calendar events. |
| 7. Does the voice assistant work completely? | **PARTIAL** | Functional STT/TTS + intents + wake-word in supported browsers. Missing true pause/resume, user prefs, robust TTS error surfacing, privacy safeguards. No backend voice service (by design). |
| 8. Do WhatsApp and web push notifications work? | **PARTIAL** | **Outbound WhatsApp**: verified. **Inbound via n8n relay**: broken (signature dropped). **Web push**: wired end-to-end with minor gaps (no expires_at, possible duplicates). |
| 9. Does n8n reliably orchestrate synchronization? | **NO** | JWT-based workflows have stale token + no refresh; persistent 401s; no retry/error branches; hostname inconsistency; WhatsApp inbound relay broken. Multi-user sync not imported. Stateful incremental path not connected. |
| 10. Is CHARUSAT attendance ready? | **PARTIAL** | Code/migrations (0012/0013) are safe and tested in isolation. Live DB at 0011 (missing schema/columns), frontend build predates card, backend process has stale in-memory code. **Ready after** applying 0013 → restart backend → rebuild frontend, with ordering constraint. End-to-end live portal read remains unverified (student must authenticate to portal). |
| 11. What is the single most important blocker? | **Gmail sync completeness + pagination** | Without multi-page traversal, the system cannot claim to read "all emails" in scope; many older/relevant messages are never ingested or analyzed. This undermines the core "email intelligence" value proposition. |
| 12. What should be fixed first? | **P0 items + ordering for attendance/DB** | (1) Fix n8n JWT/relay critical issues; (2) Enforce non-fail-open auth in non-prod; (3) **Apply migration 0013 before backend restart** (schema/code ordering), rebuild frontend; (4) Implement Gmail pagination end-to-end. |

**Overall verdict:** **WORKS WITH LIMITATIONS** — strong foundation (auth, isolation, AI, local Ollama), but production readiness is blocked by Gmail completeness, calendar sync completeness, n8n reliability, and a few security hardenings. With P0/P1 remediation in the stated order, the system can become reliably usable.

---

*This audit was performed read-only. No files modified, no live DB migrated, no containers restarted/rebuilt, no external messages sent. All evidence includes file paths and line numbers where cited. Docker daemon was unavailable during inspection, limiting live runtime verification.*