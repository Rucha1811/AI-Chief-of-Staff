"use client";

import { useEffect, useRef, useState } from "react";
import { api, Email, EmailStats, GoogleAuthHealth } from "@/lib/api";
import { Mail, Star, AlertCircle, MessageSquare, Sparkles, RefreshCw, Download, ChevronDown, ChevronUp, Settings } from "lucide-react";
import Link from "next/link";
import BackButton from "@/components/BackButton";

export default function EmailsPage() {
  const [emails, setEmails] = useState<Email[]>([]);
  const [stats, setStats] = useState<EmailStats | null>(null);
  const [health, setHealth] = useState<GoogleAuthHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);
  const [filter, setFilter] = useState<string | undefined>();
  const [selectedEmail, setSelectedEmail] = useState<Email | null>(null);
  const [aiWorking, setAiWorking] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [e, s] = await Promise.all([api.emails.list({ category: filter, limit: 200 }), api.emails.stats()]);
      setEmails(e);
      setStats(s);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load emails");
    } finally {
      setLoading(false);
    }
  };

  const checkHealth = async () => {
    try {
      setHealth(await api.emails.health());
    } catch {
      /* keep existing health */
    }
  };

  useEffect(() => { load(); }, [filter]);
  useEffect(() => { checkHealth(); }, []);

  const autoSyncRan = useRef(false);
  useEffect(() => {
    if (autoSyncRan.current) return;
    autoSyncRan.current = true;
    api.emails.sync(1000).then((result) => {
      if (result.added > 0) load();
    }).catch(() => {});
  }, []);

  const syncGmail = async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const result = await api.emails.sync(1000);
      setSyncMsg(result.added > 0 ? `Imported ${result.added} new emails` : "No new emails");
      await load();
    } catch (err: any) {
      setSyncMsg(err.message || "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const processAll = async () => {
    setProcessing(true);
    setSyncMsg(null);
    try {
      const result = await api.emails.processAll(1000);
      setSyncMsg(`Processed ${result.processed} emails with AI${result.failed ? ` (${result.failed} failed)` : ""}${result.remaining ? `, ${result.remaining} remaining` : ""}`);
      await load();
    } catch (err: any) {
      setSyncMsg(err.message || "Processing failed");
    } finally {
      setProcessing(false);
    }
  };

  const classify = async (id: number) => {
    setAiWorking(id);
    await api.emails.classify(id);
    await load();
    setAiWorking(null);
  };

  const suggestReply = async (id: number) => {
    setAiWorking(id);
    const result = await api.emails.suggestReply(id);
    setEmails((prev) => prev.map((e) => (e.id === id ? { ...e, suggested_reply: result.suggested_reply } : e)));
    if (selectedEmail?.id === id) setSelectedEmail((prev) => prev ? { ...prev, suggested_reply: result.suggested_reply } : null);
    setAiWorking(null);
  };

  const summarize = async (id: number) => {
    setAiWorking(id);
    const result = await api.emails.summarize(id);
    setEmails((prev) => prev.map((e) => (e.id === id ? { ...e, summary: result.summary, has_deadline: result.has_deadline, deadline_description: result.deadline_description } : e)));
    if (selectedEmail?.id === id) setSelectedEmail((prev) => prev ? { ...prev, summary: result.summary, has_deadline: result.has_deadline, deadline_description: result.deadline_description } : null);
    setAiWorking(null);
  };

  const categoryColors: Record<string, string> = {
    reply_required: "badge-critical",
    important: "badge-high",
    can_ignore: "badge-low",
    promotion: "badge-promotion",
    social: "badge-social",
  };

  return (
    <div className="space-y-6">
      {error && (
        <div className="card border-amber-200 bg-amber-50 flex items-center gap-3">
          <AlertCircle className="w-4 h-4 text-amber-600 shrink-0" />
          <p className="text-sm text-amber-700 flex-1">{error}</p>
          <button onClick={() => window.location.reload()} className="btn-secondary text-xs">Retry</button>
        </div>
      )}
      <div className="flex items-center justify-between">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3">Email Intelligence</h1>
          <p className="text-slate-500 mt-1">
            {stats?.total ?? 0} emails · {stats?.reply_required ?? 0} need reply · {stats?.unread ?? 0} unread
          </p>
        </div>
        <button onClick={load} className="btn-secondary flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
        <button onClick={syncGmail} disabled={syncing} className="btn-primary flex items-center gap-2">
          <Download className="w-4 h-4" /> {syncing ? "Syncing..." : "Sync from Gmail"}
        </button>
        <button onClick={processAll} disabled={processing} className="btn-secondary flex items-center gap-2">
          <Sparkles className="w-4 h-4" /> {processing ? "Processing..." : "Process All (AI)"}
        </button>
      </div>

      {syncMsg && (
        <div className="text-xs text-slate-500 bg-slate-100 p-2 rounded-lg">{syncMsg}</div>
      )}

      {health && (
        <div className={`card flex items-center gap-3 ${health.connected ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50"}`}>
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${health.connected ? "bg-emerald-500" : "bg-red-500"}`} />
          <div className="flex-1">
            <p className="text-sm font-medium text-slate-800">
              Google connection {health.connected ? "active" : "needs attention"}
              {health.email ? ` · ${health.email}` : ""}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              {health.connected
                ? "Gmail, Classroom and Calendar syncing is working."
                : health.reason === "not_authorized"
                  ? "No Google account connected yet."
                  : "Your Google authorization expired and can't be refreshed (common for unverified/testing OAuth apps after 7 days)."}
            </p>
          </div>
          {!health.connected && (
            <Link href="/settings" className="btn-primary text-xs shrink-0 flex items-center gap-1.5">
              <Settings className="w-3.5 h-3.5" /> Connect in Settings
            </Link>
          )}
        </div>
      )}

      <div className="grid grid-cols-4 gap-3">
        <button onClick={() => setFilter(undefined)} className={`card text-center py-3 ${!filter ? "border-pink-400" : ""}`}>
          <div className="stat-value text-lg text-slate-900">{stats?.total ?? 0}</div>
          <div className="text-xs text-slate-500">All</div>
        </button>
        <button onClick={() => setFilter("reply_required")} className={`card text-center py-3 ${filter === "reply_required" ? "border-pink-400" : ""}`}>
          <div className="stat-value text-lg text-red-600">{stats?.reply_required ?? 0}</div>
          <div className="text-xs text-slate-500">Reply Required</div>
        </button>
        <button onClick={() => setFilter("important")} className={`card text-center py-3 ${filter === "important" ? "border-pink-400" : ""}`}>
          <div className="stat-value text-lg text-orange-600">{stats?.important ?? 0}</div>
          <div className="text-xs text-slate-500">Important</div>
        </button>
        <button onClick={() => setFilter("can_ignore")} className={`card text-center py-3 ${filter === "can_ignore" ? "border-pink-400" : ""}`}>
          <div className="stat-value text-lg text-slate-500">{stats?.can_ignore ?? 0}</div>
          <div className="text-xs text-slate-500">Can Ignore</div>
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2">
          {emails.map((email) => (
            <div key={email.id}>
              <button
                onClick={() => setSelectedEmail(selectedEmail?.id === email.id ? null : email)}
                className={`w-full card text-left hover:bg-slate-100 transition-colors ${!email.is_read ? "border-l-2 border-l-pink-500" : ""} ${selectedEmail?.id === email.id ? "ring-1 ring-pink-400" : ""}`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-800 truncate">{email.from_name || email.from_email}</span>
                      {email.category && (
                        <span className={categoryColors[email.category] || "badge"}>{email.category.replace("_", " ")}</span>
                      )}
                      {email.has_deadline && <AlertCircle className="w-3.5 h-3.5 text-red-600" />}
                      {email.is_starred && <Star className="w-3.5 h-3.5 text-amber-600 fill-amber-400" />}
                    </div>
                    <p className="text-sm text-slate-600 truncate mt-0.5">{email.subject}</p>
                    {email.summary && <p className="text-xs text-slate-500 mt-0.5 truncate">{email.summary}</p>}
                  </div>
                  <div className="flex items-center gap-1 ml-4">
                    <span className="text-xs text-slate-500 whitespace-nowrap">
                      {email.received_at ? new Date(email.received_at).toLocaleDateString() : ""}
                    </span>
                  </div>
                </div>
              </button>

              {selectedEmail?.id === email.id && (
                <div className="card mt-1 border-pink-200 space-y-4">
                  <div className="flex gap-2 flex-wrap">
                    <button onClick={() => classify(email.id)} disabled={aiWorking === email.id} className="btn-secondary flex items-center gap-1.5 text-xs">
                      <Sparkles className="w-3.5 h-3.5" /> {aiWorking === email.id ? "..." : "Classify"}
                    </button>
                    <button onClick={() => summarize(email.id)} disabled={aiWorking === email.id} className="btn-secondary flex items-center gap-1.5 text-xs">
                      <Sparkles className="w-3.5 h-3.5" /> {aiWorking === email.id ? "..." : "Summarize"}
                    </button>
                    <button onClick={() => suggestReply(email.id)} disabled={aiWorking === email.id} className="btn-secondary flex items-center gap-1.5 text-xs">
                      <MessageSquare className="w-3.5 h-3.5" /> {aiWorking === email.id ? "..." : "Suggest Reply"}
                    </button>
                    <button onClick={() => api.emails.toggleStar(email.id).then(load)} className="btn-ghost text-xs">Star</button>
                    <button onClick={() => api.emails.markRead(email.id).then(load)} className="btn-ghost text-xs">Mark Read</button>
                  </div>

                  {email.summary && (
                    <div>
                      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Summary</h3>
                      <p className="text-sm text-slate-600">{email.summary}</p>
                    </div>
                  )}

                  {email.has_deadline && email.deadline_description && (
                    <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 p-2 rounded-lg">
                      <AlertCircle className="w-4 h-4" />
                      Deadline: {email.deadline_description}
                    </div>
                  )}

                  {email.suggested_reply && (
                    <div>
                      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Suggested Reply</h3>
                      <div className="bg-slate-50 p-3 rounded-lg text-sm text-slate-600 whitespace-pre-wrap">
                        {email.suggested_reply}
                      </div>
                      <button className="btn-primary text-xs mt-2" onClick={() => navigator.clipboard.writeText(email.suggested_reply || "")}>
                        Copy to Clipboard
                      </button>
                    </div>
                  )}

                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Original</h3>
                    <p className="text-sm text-slate-500 whitespace-pre-wrap line-clamp-6">{email.body_text}</p>
                  </div>
                </div>
              )}
            </div>
          ))}

          {emails.length === 0 && (
            <div className="card text-center py-12 text-slate-500">
              <Mail className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No emails found</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
