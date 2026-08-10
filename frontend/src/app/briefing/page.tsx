"use client";

import { useEffect, useState } from "react";
import { api, Briefing as BriefingType } from "@/lib/api";
import { Newspaper, Calendar, Mail, CheckSquare, Lightbulb, RefreshCw } from "lucide-react";
import Link from "next/link";
import BackButton from "@/components/BackButton";

export default function BriefingPage() {
  const [briefing, setBriefing] = useState<BriefingType | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (refresh = false) => {
    setLoading(true);
    try {
      const data = await api.briefing.today(refresh);
      setBriefing(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load briefing");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const regenerate = async () => {
    setRegenerating(true);
    await load(true);
    setRegenerating(false);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <BackButton />
        <div className="card border-amber-200 bg-amber-50">
          <p className="text-sm text-amber-700">{error}</p>
        </div>
        <button onClick={regenerate} className="btn-primary">Try again</button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3">
            {briefing?.greeting || "Good Morning!"}
          </h1>
          <p className="text-slate-500 mt-1">
            {new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}
          </p>
        </div>
        <button onClick={regenerate} disabled={regenerating} className="btn-secondary flex items-center gap-2">
          <RefreshCw className={`w-4 h-4 ${regenerating ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="card">
        <p className="text-slate-800 leading-relaxed">{briefing?.summary}</p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <Link href="/calendar" className="card flex items-center gap-3 hover:bg-slate-100 transition-colors">
          <Calendar className="w-8 h-8 text-emerald-600" />
          <div>
            <div className="text-2xl font-bold text-slate-900">{briefing?.meetings_count ?? 0}</div>
            <div className="text-xs text-slate-500">Meetings Today</div>
          </div>
        </Link>
        <Link href="/emails" className="card flex items-center gap-3 hover:bg-slate-100 transition-colors">
          <Mail className="w-8 h-8 text-blue-600" />
          <div>
            <div className="text-2xl font-bold text-slate-900">{briefing?.important_emails_count ?? 0}</div>
            <div className="text-xs text-slate-500">Important Emails</div>
          </div>
        </Link>
        <Link href="/tasks" className="card flex items-center gap-3 hover:bg-slate-100 transition-colors">
          <CheckSquare className="w-8 h-8 text-amber-600" />
          <div>
            <div className="text-2xl font-bold text-slate-900">{briefing?.pending_tasks_count ?? 0}</div>
            <div className="text-xs text-slate-500">Pending Tasks</div>
          </div>
        </Link>
      </div>

      {briefing?.meetings_highlights && (
        <div className="card">
          <div className="flex items-center gap-2 mb-3">
            <Calendar className="w-4 h-4 text-emerald-600" />
            <h2 className="text-sm font-semibold text-slate-800">Meeting Highlights</h2>
          </div>
          <p className="text-sm text-slate-500">{briefing.meetings_highlights}</p>
        </div>
      )}

      {briefing?.email_highlights && (
        <div className="card">
          <div className="flex items-center gap-2 mb-3">
            <Mail className="w-4 h-4 text-blue-600" />
            <h2 className="text-sm font-semibold text-slate-800">Email Highlights</h2>
          </div>
          <p className="text-sm text-slate-500">{briefing.email_highlights}</p>
        </div>
      )}

      {briefing?.task_highlights && (
        <div className="card">
          <div className="flex items-center gap-2 mb-3">
            <CheckSquare className="w-4 h-4 text-amber-600" />
            <h2 className="text-sm font-semibold text-slate-800">Task Highlights</h2>
          </div>
          <p className="text-sm text-slate-500">{briefing.task_highlights}</p>
        </div>
      )}

      {briefing?.suggested_plan && (
        <div className="card border-pink-200">
          <div className="flex items-center gap-2 mb-3">
            <Lightbulb className="w-4 h-4 text-pink-600" />
            <h2 className="text-sm font-semibold text-slate-800">Suggested Plan</h2>
          </div>
          <pre className="text-sm text-slate-600 whitespace-pre-wrap font-sans">
            {briefing.suggested_plan}
          </pre>
        </div>
      )}

      {briefing?.news_items && briefing.news_items.length > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 mb-3">
            <Newspaper className="w-4 h-4 text-purple-600" />
            <h2 className="text-sm font-semibold text-slate-800">Top Technology News</h2>
          </div>
          <div className="space-y-3">
            {briefing.news_items.map((item, i) => (
              <div key={i} className="border-b border-slate-200 pb-2 last:border-0 last:pb-0">
                <h3 className="text-sm font-medium text-slate-800">{item.title}</h3>
                {item.summary && (
                  <p className="text-xs text-slate-500 mt-0.5">{item.summary}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
