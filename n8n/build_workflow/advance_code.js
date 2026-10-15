// AI Chief: persist the sync checkpoint ONLY after the ingest POST succeeded.
// Reads the page control info from the Emit IDs node (same item order as POST Ingest outputs).
const sd = $getWorkflowStaticData('global');
const emitters = $('Emit IDs').all();
const items = $input.all();
let last = null;
for (let i = 0; i < items.length; i++) {
  const e = (emitters[i] && emitters[i].json) || {};
  const page = e.page || {};
  const plan = e.plan || {};
  if (page.hasMore) {
    sd.mode = plan.kind === 'history' ? 'incremental' : 'first';
    sd.pageToken = page.nextPageToken || null;
  } else {
    sd.mode = 'incremental';
    sd.historyId = String(page.historyId || page.profileHistoryId);
    sd.pageToken = null;
    sd.historyTs = Date.now();
  }
  last = {
    mode: sd.mode,
    historyId: sd.historyId || null,
    pageToken: sd.pageToken || null,
    hasMore: !!page.hasMore,
    kind: plan.kind || null,
    emailsPosted: items.length,
  };
}
return [{ json: { ok: true, committedBatches: items.length, state: JSON.parse(JSON.stringify({ mode: sd.mode, historyId: sd.historyId || null, pageToken: sd.pageToken || null, historyTs: sd.historyTs || null })), last } }];