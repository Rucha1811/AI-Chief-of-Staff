"use client";

import { useEffect, useState } from "react";
import { api, Email, EmailStats } from "@/lib/api";
import { Mail, Star, AlertCircle, MessageSquare, Sparkles, RefreshCw, ChevronDown, ChevronUp } from "lucide-react";

export default function EmailsPage() {
  const [emails, setEmails] = useState<Email[]>([]);
  const [stats, setStats] = useState<EmailStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string | undefined>();
  const [selectedEmail, setSelectedEmail] = useState<Email | null>(null);
  const [aiWorking, setAiWorking] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [e, s] = await Promise.all([api.emails.list({ category: filter, limit: 50 }), api.emails.stats()]);
      setEmails(e);
      setStats(s);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [filter]);

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
    promotion: "badge-medium",
    social: "badge-low",
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Email Intelligence</h1>
          <p className="text-slate-400 mt-1">
            {stats?.total ?? 0} emails · {stats?.reply_required ?? 0} need reply · {stats?.unread ?? 0} unread
          </p>
        </div>
        <button onClick={load} className="btn-secondary flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <button onClick={() => setFilter(undefined)} className={`card text-center py-3 ${!filter ? "border-indigo-600" : ""}`}>
          <div className="stat-value text-lg text-slate-100">{stats?.total ?? 0}</div>
          <div className="text-xs text-slate-400">All</div>
        </button>
        <button onClick={() => setFilter("reply_required")} className={`card text-center py-3 ${filter === "reply_required" ? "border-indigo-600" : ""}`}>
          <div className="stat-value text-lg text-red-400">{stats?.reply_required ?? 0}</div>
          <div className="text-xs text-slate-400">Reply Required</div>
        </button>
        <button onClick={() => setFilter("important")} className={`card text-center py-3 ${filter === "important" ? "border-indigo-600" : ""}`}>
          <div className="stat-value text-lg text-orange-400">{stats?.important ?? 0}</div>
          <div className="text-xs text-slate-400">Important</div>
        </button>
        <button onClick={() => setFilter("can_ignore")} className={`card text-center py-3 ${filter === "can_ignore" ? "border-indigo-600" : ""}`}>
          <div className="stat-value text-lg text-slate-500">{stats?.can_ignore ?? 0}</div>
          <div className="text-xs text-slate-400">Can Ignore</div>
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2">
          {emails.map((email) => (
            <div key={email.id}>
              <button
                onClick={() => setSelectedEmail(selectedEmail?.id === email.id ? null : email)}
                className={`w-full card text-left hover:bg-slate-800/70 transition-colors ${!email.is_read ? "border-l-2 border-l-indigo-500" : ""} ${selectedEmail?.id === email.id ? "ring-1 ring-indigo-600" : ""}`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-200 truncate">{email.from_name || email.from_email}</span>
                      {email.category && (
                        <span className={categoryColors[email.category] || "badge"}>{email.category.replace("_", " ")}</span>
                      )}
                      {email.has_deadline && <AlertCircle className="w-3.5 h-3.5 text-red-400" />}
                      {email.is_starred && <Star className="w-3.5 h-3.5 text-amber-400 fill-amber-400" />}
                    </div>
                    <p className="text-sm text-slate-300 truncate mt-0.5">{email.subject}</p>
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
                <div className="card mt-1 border-indigo-700/50 space-y-4">
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
                      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Summary</h3>
                      <p className="text-sm text-slate-300">{email.summary}</p>
                    </div>
                  )}

                  {email.has_deadline && email.deadline_description && (
                    <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 p-2 rounded-lg">
                      <AlertCircle className="w-4 h-4" />
                      Deadline: {email.deadline_description}
                    </div>
                  )}

                  {email.suggested_reply && (
                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Suggested Reply</h3>
                      <div className="bg-slate-900/50 p-3 rounded-lg text-sm text-slate-300 whitespace-pre-wrap">
                        {email.suggested_reply}
                      </div>
                      <button className="btn-primary text-xs mt-2" onClick={() => navigator.clipboard.writeText(email.suggested_reply || "")}>
                        Copy to Clipboard
                      </button>
                    </div>
                  )}

                  <div>
                    <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Original</h3>
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
