// AI Chief: normalize a full Gmail message into an EmailIngestItem
const items = $input.all();
const out = [];
for (const item of items) {
  const msg = item.json || {};
  const plan = msg.plan || {};

  const headers = (msg.payload && msg.payload.headers) || [];
  const getHeader = (n) => {
    const h = headers.find((x) => String(x.name || '').toLowerCase() === n.toLowerCase());
    return h ? String(h.value || '') : '';
  };
  const fromRaw = getHeader('From');
  const mf = fromRaw.match(/^([^<]*)\s*<([^>]+)>/);
  let fromEmail = fromRaw.trim();
  let fromName = '';
  if (mf) { fromName = mf[1].replace(/^["']|["']$/g, '').trim(); fromEmail = mf[2].trim(); }
  const toRaw = getHeader('To');

  const decodeB64 = (s) => {
    if (!s) return '';
    const b = String(s).replace(/-/g, '+').replace(/_/g, '/');
    try { return Buffer.from(b, 'base64').toString('utf8'); } catch (e) { return ''; }
  };
  const extractText = (payload, acc) => {
    if (!payload) return;
    const mime = String(payload.mimeType || '').toLowerCase();
    if (payload.body && payload.body.data) {
      if (mime === 'text/plain') acc.plain.push(decodeB64(payload.body.data));
      else if (mime === 'text/html') acc.html.push(decodeB64(payload.body.data));
    }
    for (const p of (payload.parts || [])) extractText(p, acc);
  };
  const stripHtml = (h) => String(h || '')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ').trim();
  const acc = { plain: [], html: [] };
  extractText(msg.payload, acc);
  const bodyText = acc.plain.join('\n').trim() || stripHtml(acc.html.join(' ')) || String(msg.snippet || '');
  const receivedAt = msg.internalDate ? new Date(Number(msg.internalDate)).toISOString() : new Date().toISOString();
  const isRead = !(msg.labelIds || []).includes('UNREAD');

  const email = {
    message_id: String(msg.id),
    thread_id: msg.threadId ? String(msg.threadId) : null,
    from_email: fromEmail || null,
    from_name: fromName || null,
    to_email: toRaw ? toRaw.split(',').map((s) => s.trim()).join(', ') : null,
    subject: getHeader('Subject') || '',
    body_text: bodyText,
    received_at: receivedAt,
    is_read: isRead,
  };
  out.push({ json: { msg: email, ingestPath: plan.ingestPath || '/api/integrations/emails/ingest', baseUrl: plan.baseUrl || 'http://host.docker.internal:8000' } });
}
return out;