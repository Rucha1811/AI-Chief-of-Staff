// AI Chief: decide sync mode from checkpoint state + a fresh Gmail profile check.
// kind 'history' -> users.history.list(startHistoryId) incremental
// kind 'list'    -> users.messages.list (brand-new full sync OR retention-window fallback)
const profile = $input.all()[0]?.json || {};
const config = $('Fetch Config').first().json || {};
const sd = $getWorkflowStaticData('global');

const batchMax = Number(config.ingest_batch_max) || 100;
const retentionDays = Number(config.email_retention_days) || 90;
const profileHistoryId = profile.historyId ? String(profile.historyId) : null;
const GM_BASE = 'https://www.googleapis.com/gmail/v1/users/me';
const INGEST_BASE = 'http://host.docker.internal:8000';

const histNum = sd.historyId ? Number(sd.historyId) : NaN;
const histNumericOk = Number.isInteger(histNum) && histNum > 0;
const histFreshOk = sd.historyTs ? (Date.now() - Number(sd.historyTs)) < 30 * 24 * 3600 * 1000 : false;
const histNotFutureOk = profileHistoryId ? histNum <= Number(profileHistoryId) : true;

let kind = 'list';
let qs = { maxResults: batchMax };

if (sd.mode === 'incremental' && histNumericOk && histFreshOk && histNotFutureOk) {
  // Healthy checkpoint -> true incremental via history.list
  kind = 'history';
  qs = { startHistoryId: String(sd.historyId), historyTypes: 'messageAdded', maxResults: batchMax };
  if (sd.pageToken) qs.pageToken = sd.pageToken;
} else if (sd.pageToken) {
  // Continue paginating an in-flight list scan (first sync or window fallback)
  if (sd.mode === 'incremental') qs.q = 'newer_than:' + retentionDays + 'd';
  qs.pageToken = sd.pageToken;
} else if (sd.mode === 'incremental') {
  // Stale or invalid historyId -> bounded retention-window resync, then re-establish checkpoint
  qs.q = 'newer_than:' + retentionDays + 'd';
} else {
  // Brand new workflow -> full paginated first sync
}

// Persist a snapshot of the plan so downstream nodes (whose HTTP outputs drop
// input fields) can still reconstruct the sync context.
const syncPlan = {
  kind,
  base: GM_BASE,
  profileHistoryId,
  batchMax,
  retentionDays,
  ingestPath: config.ingest_endpoint || '/api/integrations/emails/ingest',
  baseUrl: INGEST_BASE,
};
sd.syncPlan = syncPlan;

return [{
  json: {
    plan: {
      kind,
      path: kind === 'history' ? 'history' : 'messages',
      qs,
      base: GM_BASE,
      profileHistoryId,
      batchMax,
      retentionDays,
      ingestPath: syncPlan.ingestPath,
      baseUrl: INGEST_BASE,
    },
  },
}];