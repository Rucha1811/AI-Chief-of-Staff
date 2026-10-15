# n8n workflows

Importable HTTP-only workflows that orchestrate against the authenticated
FastAPI backend. No native Gmail/Classroom nodes (Desktop OAuth client is not
compatible with n8n's web-app callback). No secrets are stored in these files.

## Files

- `ai-chief-phase1-ingest-poc.json` — Manual-trigger POC proving the
  sync → read → normalize → ingest (dedup) → report round trip.
- `ai-chief-phase2-monitor.json` — Scheduled monitor (every 15 min):
  fetch recent → fetch important → IF needs-attention → summarize → log.
- `ai-chief-email-sync.json` — **Multi-user email sync orchestrator** (generated
  from `build_workflow/build.py`). For each trusted Google account reported by
  `GET /api/integrations/agent/accounts`, it calls `POST /api/integrations/agent/sync`.
  Identity is carried purely as `account_id` (resolved server-side); n8n holds
  **no Gmail credential** and submits no numeric `user_id`.

## Workflow classification (multi-user audit)

| Workflow                    | Status        | Class               | Notes |
|-----------------------------|---------------|---------------------|-------|
| `ai-chief-phase2-monitor.json` | Active in n8n | SINGLE-USER (owner) | Read-only health monitor, owner JWT; active version has no digest |
| `ai-chief-email-sync.json`    | Generated (not imported live; supersedes the old Gmail→ingest) | MULTI-USER | Backend does all Gmail fetch with per-user creds |
| WhatsApp Inbound (in n8n)    | Active in n8n | SINGLE-USER (owner) | Twilio number → org owner; signature verified server-side |
| `ai-chief-phase1-ingest-poc.json` | Inactive in n8n | UNUSED-LEGACY | Kept for reference; do not activate |

`build_workflow/*_code.js` (decide/emit/normalize/fetch/advance) are the source
for the retired Gmail-fetch pipeline (n8n doing Gmail itself). That approach
was dropped because n8n has a single global credential store and cannot select
per-user OAuth credentials — Gmail access belongs to the backend, which stores
one credential per user. See `ARCHITECTURE.md`.

## Runtime requirements (Phase 2 must all hold)

1. n8n is on the shared `aichief_net` network so `backend` resolves by name
   (`docker-compose.n8n.yml`, external network, service name `backend`).
2. `AI_CHIEF_BACKEND_URL=http://backend:8000` is set for the n8n container and
   is consumed via `{{ $env.AI_CHIEF_BACKEND_URL }}` — nothing is baked in.
3. `N8N_BLOCK_ENV_ACCESS_IN_NODE: "false"` — otherwise every expression that
   reads `$env.*` fails with `access to env vars denied`.
4. The n8n `httpBearerAuth` credential "AI Chief Backend JWT" must contain a
   **fresh** JWT (`POST /api/auth/login` with the account credentials returns
   one — never paste a real password into this file). Tokens expire weekly; an
   expired token shows 401 on the sync/fetch nodes.
5. The phase-1 `httpHeaderAuth` "AI Chief Ingest API" credential holds the
   backend `x-api-key` from `backend/.ingest_key`.

## Credential & node layout

| Credential name        | Type           | Used by          |
|------------------------|----------------|------------------|
| `AI Chief Backend JWT` | httpBearerAuth | Phase-2 monitor sync/fetch nodes (owner-scoped JWT) |
| `AI Chief Ingest API`  | httpHeaderAuth | Ingest node **and** the multi-user sync workflow (list accounts + per-account sync) |

The multi-user sync workflow (`ai-chief-email-sync.json`) uses **only** the
`AI Chief Ingest API` header-auth credential — all its calls are
server-to-server and gated by the ingest key. No user JWT is involved, so that
workflow never expires and never impersonates one fixed user.

Phase 2 node 1 hits `GET /api/emails?limit=20` (reads the persisted pipeline
state), node 2 `GET /api/emails?limit=50&category=important`. Both return 200
from the DB, so the scheduled monitor completes green even while the live
Gmail sync (`POST /api/emails/sync`) is down awaiting Gmail re-authorization.

## Import + activation (automated path used in this project)

The n8n REST API requires an owner session (password not recoverable), so the
actual import used the CLI + an offline SQLite patch:

1. Start the stack:
   ```bash
   docker compose up -d
   docker compose -f docker-compose.n8n.yml up -d
   ```
2. Import with an explicit workflow `id`, `--projectId`, and all node
   `credentials` stripped to `{}` (otherwise the CLI errors
   `credential with ID "undefined" is already owned`):
   ```bash
   docker exec n8n-n8n-1 n8n import:workflow \
     --input=/dev/stdin --projectId=viPjt3Uw3ZSdutTR
   ```
3. n8n 2.x only treats a workflow as active when `activeVersionId` points to a
   `workflow_published_version` row. Offline DB patch (SQLite):
   - insert a `workflow_published_version` row for the workflow,
   - copy the workflow into `workflow_history` and point `activeVersionId` at it,
   - set `active=1` and `triggerCount=1`,
   - add the credential references back onto the HTTP nodes
     (`credentials: {httpBearerAuth: {id: "YMHqqCpBTFTawkdz", ...}}`).
4. Restart n8n; the log should show
   `Activated workflow "AI CHIEF · Phase 2 Monitor" (ID: aichiefPh2Mnt01)`.
5. Runs every 15 min appear in `execution_entity` (`mode=trigger`).

When editing the running DB instead of re-importing, remember the runtime
reads the **active version** in `workflow_history` (and n8n re-serialises that
row's JSON itself), never hand-concatenate JSON fragments into those columns,
and always delete stale `database.sqlite-wal`/`-shm` before overwriting the
DB file, then `chown 1000:1000` it.