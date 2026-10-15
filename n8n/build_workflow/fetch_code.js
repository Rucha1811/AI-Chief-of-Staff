// AI Chief: Gmail fetch -> normalize -> batch for the ingest API.
// One page per run, restartable; checkpoint persisted only AFTER a successful ingest POST.

const config = $input.all()[0]?.json || {};
const baseUrl = 'http://host.docker.internal:8000';
const ingestUrl = baseUrl + (config.ingest_endpoint || '/api/integrations/emails/ingest');
const batchMax = Number(config.ingest_batch_max) || 50;

function decodeB64(s) {
  if (!s) return '';
  const b = String(s).replace(/-/g, '+').replace(/_/g, '/');
  try { return Buffer.from(b, 'base64').toString('utf8'); } catch (e) { return ''; }
}

function extractText(payload, acc) {
  if (!payload) return;
  const mime = String(payload.mimeType || '').toLowerCase();
  if (payload.body && payload.body.data) {
    if (mime === 'text/plain') acc.plain.push(decodeB64(payload.body.data));
    else if (mime === 'text/html') acc.html.push(decodeB64(payload.body.data));
  }
  for (const p of (payload.parts || [])) extractText(p, acc);
}

function stripHtml(h) {
  return String(h || '')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ').trim();
}

function extractBody(payload) {
  const acc = { plain: [], html: [] };
  extractText(payload, acc);
  const plain = acc.plain.join('\n').trim();
  return plain || stripHtml(acc.html.join(' '));
}

function normalizeMsg(msg) {
  const headers = (msg.payload && msg.payload.headers) || [];
  const getHeader = (n) => {
    const h = headers.find((x) => String(x.name || '').toLowerCase() === n.toLowerCase());
    return h ? String(h.value || '') : '';
  };
  const fromRaw = getHeader('From');
  const m = fromRaw.match(/^([^<]*)\s*<([^>]+)>/);
  let fromEmail = fromRaw.trim();
  let fromName = '';
  if (m) { fromName = m[1].replace(/^["']|["']$/g, '').trim(); fromEmail = m[2].trim(); }
  const toRaw = getHeader('To');
  const subject = getHeader('Subject');
  const bodyText = extractBody(msg.payload) || String(msg.snippet || '');
  const receivedAt = msg.internalDate ? new Date(Number(msg.internalDate)).toISOString() : new Date().toISOString();
  const isRead = !(msg.labelIds || []).includes('UNREAD');
  return {
    message_id: String(msg.id),
    thread_id: msg.threadId ? String(msg.threadId) : null,
    from_email: fromEmail,
    from_name: fromName || null,
    to_email: toRaw.split(',').map((s) => s.trim()).join(', '),
    subject: subject || '',
    body_text: bodyText,
    received_at: receivedAt,
    is_read: isRead,
  };
}

const gmailGet = (path, qs) =>
  this.helpers.httpRequestWithAuthentication.call(this, 'gmailOAuth2', {
    method: 'GET',
    url: 'https://www.googleapis.com/gmail/v1/users/me/' + path,
    qs,
    json: true,
  });

async function fetchFullMessages(ids) {
  const out = [];
  for (const id of ids) {
    try {
      const full = await gmailGet('messages/' + encodeURIComponent(id));
      out.push(normalizeMsg(full));
    } catch (e) { /* message deleted or unreachable between list and fetch */ }
  }
  return out;
}

const sd = $getWorkflowStaticData('global');
let emails = [];
let checkpoint = { mode: 'incremental' };
let meta = { mode: 'incremental', fallbackUsed: false, pages: 0, returned: 0, run: new Date().toISOString() };

if (sd.mode !== 'incremental') {
  // FIRST sync: paginate users.messages.list, one page per run.
  meta.mode = 'first';
  checkpoint = { mode: 'first' };
  const qs = { maxResults: batchMax };
  if (sd.pageToken) qs.pageToken = sd.pageToken;
  const resp = await gmailGet('messages', qs);
  meta.pages = 1;
  meta.returned = (resp.messages || []).length;
  emails = await fetchFullMessages((resp.messages || []).map((m) => m.id));
  if (resp.nextPageToken) {
    checkpoint.pageToken = resp.nextPageToken;
  } else {
    const prof = await gmailGet('profile');
    checkpoint.mode = 'incremental';
    checkpoint.historyId = String(prof.historyId);
    checkpoint.pageToken = null;
  }
} else {
  // INCREMENTAL: users.history.list(startHistoryId) for messageAdded events.
  try {
    const qs = { startHistoryId: String(sd.historyId), historyTypes: 'messageAdded', maxResults: batchMax };
    if (sd.pageToken) qs.pageToken = sd.pageToken;
    const resp = await gmailGet('history', qs);
    meta.pages = 1;
    meta.returned = (resp.history || []).length;
    const ids = [];
    for (const en of (resp.history || [])) {
      for (const m of (en.messages || [])) {
        if (m.id && !ids.includes(String(m.id))) ids.push(String(m.id));
      }
    }
    const budget = ids.slice(0, batchMax);
    checkpoint = { mode: 'incremental' };
    if (resp.nextPageToken) {
      checkpoint.pageToken = resp.nextPageToken;
    } else {
      checkpoint.historyId = String(resp.historyId);
      checkpoint.pageToken = null;
    }
    emails = await fetchFullMessages(budget);
  } catch (e) {
    // Invalid/expired historyId -> fallback: windowed full sync, then re-establish checkpoint.
    meta.fallbackUsed = true;
    const windowDays = Number(config.email_retention_days) || 90;
    const MAX_PAGES = 10;
    const seen = new Set();
    let pageToken = null;
    for (let pg = 0; pg < MAX_PAGES; pg++) {
      const qs = { q: 'newer_than:' + windowDays + 'd', maxResults: batchMax };
      if (pageToken) qs.pageToken = pageToken;
      const resp = await gmailGet('messages', qs);
      meta.pages = pg + 1;
      const fresh = (resp.messages || []).filter((m) => !seen.has(m.id));
      fresh.forEach((m) => seen.add(m.id));
      emails = emails.concat(await fetchFullMessages(fresh.map((m) => m.id)));
      meta.returned += (resp.messages || []).length;
      if (resp.nextPageToken) { pageToken = resp.nextPageToken; } else { break; }
    }
    const prof = await gmailGet('profile');
    checkpoint = { mode: 'incremental', historyId: String(prof.historyId), pageToken: null };
  }
}

// Split into ingest batches (max config.ingest_batch_max per request).
const chunk = (arr, size) => {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out.length ? out : [[]];
};

const batches = chunk(emails, batchMax);
return batches.map((emailsChunk) => ({
  json: {
    emails: emailsChunk,
    checkpoint: JSON.parse(JSON.stringify(checkpoint)),
    ingestUrl,
    meta,
  },
}));