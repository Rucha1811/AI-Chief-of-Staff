// AI Chief: flatten a messages.list or history.list response into one item per message id
const syncPlan = $getWorkflowStaticData('global').syncPlan || {};
const items = $input.all();
const out = [];
for (const item of items) {
  const src = item.json || {};
  const plan = { kind: syncPlan.kind, base: syncPlan.base, profileHistoryId: syncPlan.profileHistoryId || null };
  const ids = [];
  if (Array.isArray(src.history)) {
    for (const en of src.history) {
      for (const m of (en.messages || [])) {
        if (m && m.id && !ids.includes(String(m.id))) ids.push(String(m.id));
      }
    }
  } else if (Array.isArray(src.messages)) {
    for (const m of src.messages) {
      if (m && m.id && !ids.includes(String(m.id))) ids.push(String(m.id));
    }
  }
  const cap = Number(plan.batchMax) || 100;
  const capped = ids.slice(0, cap);
  const page = {
    hasMore: !!(src.nextPageToken) || ids.length > cap,
    nextPageToken: src.nextPageToken || null,
    historyId: src.historyId || null,
    profileHistoryId: plan.profileHistoryId || null,
  };
  for (const id of capped) {
    out.push({ json: { id: String(id), plan, page } });
  }
}
return out;