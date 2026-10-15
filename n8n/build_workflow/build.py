#!/usr/bin/env python3
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
OUT_DIR = ROOT.parent / "workflows"

# Live credential in the running n8n instance (docker project "n8n").
# Keep IDs in sync with what n8n stores; the header-auth credential carries
# the AI Chief ingest API key used for all server-to-server calls.
CRED_INGEST = {"id": "uPlTEMjHPTL0IZ1j", "name": "AI Chief Ingest API"}

BASE_URL = "={{ $env.AI_CHIEF_BACKEND_URL || 'http://backend:8000' }}"

select_code = """
// Each incoming item is a JSON array of trusted accounts from the backend's
// GET /api/integrations/agent/accounts. Emit one item per account so the
// sync node runs once per user with that user's account_id only.
const payload = $input.all()[0].json;
if (!Array.isArray(payload)) return [{ json: [] }];
return payload.map((a) => ({
  json: { account_id: a.account_id, user_id: a.user_id, email: a.email },
}));
"""

report_code = """
const runs = $input.all().map((i) => i.json);
return [{
  json: {
    runs,
    total_added: runs.reduce((s, r) => s + (r.added || 0), 0),
    at: new Date().toISOString(),
  },
}];
"""


def code_node(node_id, name, js, position):
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
        "parameters": {
            "language": "javaScript",
            "mode": "runOnceForAllItems",
            "jsCode": js,
        },
    }


def http_node(node_id, name, method, url, position, body=None):
    params = {
        "method": method,
        "url": url,
        "sendBody": bool(body),
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "responseFormat": "json",
    }
    if body is not None:
        params["contentType"] = "json"
        params["specifyBody"] = "json"
        params["jsonBody"] = body
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.5,
        "position": position,
        "parameters": params,
        "credentials": {"httpHeaderAuth": CRED_INGEST},
    }


nodes = [
    {
        "id": "MuTr1Ab2Cd3Ef4Gh",
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [0, -320],
        "parameters": {},
    },
    {
        "id": "MuSc5Ij6Kl7Mn8Op",
        "name": "Schedule Trigger",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1,
        "position": [220, -320],
        "parameters": {
            "rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}
        },
    },
    http_node(
        "MuLt9Qr0St1Uv2Wx",
        "List Accounts",
        "GET",
        BASE_URL + "/api/integrations/agent/accounts",
        [0, -120],
    ),
    code_node(
        "MuSe3Yz4Ab5Cd6Ef",
        "Select Accounts",
        select_code,
        [0, 120],
    ),
    http_node(
        "MuSy7Gh8Ij9Kl0Mn",
        "Sync Account",
        "POST",
        BASE_URL + "/api/integrations/agent/sync",
        [0, 360],
        body="={{ JSON.stringify({ account_id: $json.account_id, limit: 50, auto_process: true }) }}",
    ),
    code_node(
        "MuRp1Op2Qr3St4Uv",
        "Report",
        report_code,
        [0, 600],
    ),
]

connections = {
    "Manual Trigger": {"main": [[{"node": "List Accounts", "type": "main", "index": 0}]]},
    "Schedule Trigger": {"main": [[{"node": "List Accounts", "type": "main", "index": 0}]]},
    "List Accounts": {"main": [[{"node": "Select Accounts", "type": "main", "index": 0}]]},
    "Select Accounts": {"main": [[{"node": "Sync Account", "type": "main", "index": 0}]]},
    "Sync Account": {"main": [[{"node": "Report", "type": "main", "index": 0}]]},
}

workflow = {
    "id": "wMu2lTiUs3rSy0nC",
    "name": "AI CHIEF \u00b7 Multi-user Email Sync",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1"},
    "staticData": {},
    "pinData": {},
    "meta": {},
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
out = OUT_DIR / "ai-chief-email-sync.json"
out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False))
print("wrote", out, out.stat().st_size, "bytes")