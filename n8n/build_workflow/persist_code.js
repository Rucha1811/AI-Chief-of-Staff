// AI Chief: persist the sync checkpoint ONLY after the ingest POST succeeded.
const sd = $getWorkflowStaticData('global');
const fetchItems = $('Fetch & Batch').all();
const inItems = $input.all();
let committed = 0;
for (let i = 0; i < inItems.length; i++) {
  const src = (fetchItems[i] && fetchItems[i].json) || {};
  const cp = src.checkpoint || {};
  if (cp && (cp.historyId !== undefined || cp.pageToken !== undefined)) {
    if (cp.mode) sd.mode = cp.mode;
    if (cp.historyId !== undefined) sd.historyId = String(cp.historyId);
    if (cp.pageToken !== undefined) {
      sd.pageToken = cp.pageToken;
    } else if (cp.historyId !== undefined) {
      sd.pageToken = null;
    }
    committed++;
  }
}
return [{
  json: {
    committed,
    batches: inItems.length,
    mode: sd.mode || null,
    historyId: sd.historyId || null,
    pageToken: sd.pageToken || null,
  },
}];