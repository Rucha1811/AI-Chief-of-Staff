"use client";

import { useEffect, useState } from "react";
import { api, Dashboard as DashboardType, NewsItem } from "@/lib/api";
import {
  Mail,
  CheckSquare,
  FileText,
  AlertTriangle,
  MessageSquare,
  Newspaper,
  Clock,
  Inbox,
  Sparkles,
  ArrowRight,
  Send,
  CalendarClock,
} from "lucide-react";
import Link from "next/link";

function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 21) return "Good evening";
  return "Good night";
}

function displayName(email: string | null | undefined): string {
  if (!email) return "";
  const local = email.split("@")[0] || "";
  const m = local.match(/^(\d+)/);
  if (m) return `Student ${m[1]}`;
  const parts = local.split(/[._\-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return parts
      .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
      .slice(0, 2)
      .join(" ");
  }
  return local;
}

function fmtDue(iso: string, overdue: boolean): { label: string; overdue: boolean; near: boolean } {
  const due = new Date(iso);
  const now = Date.now();
  const diff = due.getTime() - now;
  const day = 86400000;
  if (overdue && diff < -day) return { label: `${due.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · overdue`, overdue: true, near: false };
  if (overdue) return { label: "overdue", overdue: true, near: true };
  if (diff < 0) return { label: "overdue", overdue: true, near: true };
  if (diff < day) return { label: "due today", overdue: false, near: true };
  if (diff < 7 * day) return { label: due.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }), overdue: false, near: true };
  return { label: due.toLocaleDateString("en-US", { month: "short", day: "numeric" }), overdue: false, near: false };
}

const STAT_META = [
  { key: "emails", label: "Emails", icon: Mail, chip: "bg-blue-100 text-blue-600" },
  { key: "events", label: "Today's Events", icon: CalendarClock, chip: "bg-emerald-100 text-emerald-600" },
  { key: "tasks", label: "Tasks", icon: CheckSquare, chip: "bg-amber-100 text-amber-600" },
  { key: "docs", label: "Documents", icon: FileText, chip: "bg-purple-100 text-purple-600" },
] as const;

export default function DashboardPage() {
  const [data, setData] = useState<DashboardType | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dashboard.get().then(setData).catch((e: any) => setError(e.message || "Failed to load dashboard")).finally(() => setLoading(false));
    api.news.list("technology", 4).then((d) => setNews(d.items)).catch(() => {});
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <AlertTriangle className="w-8 h-8 text-amber-500" />
        <p className="text-sm text-slate-600 max-w-md text-center">{error}</p>
        <button onClick={() => window.location.reload()} className="btn-primary">
          Retry
        </button>
      </div>
    );
  }

  const overdueTasks = data?.task_stats.overdue ?? 0;
  const stats = [
    { key: "emails", label: "Emails", value: data?.email_stats.total ?? 0, sub: `${data?.email_stats.unread ?? 0} unread · ${data?.email_stats.reply_required ?? 0} need reply`, href: "/emails" },
    { key: "events", label: "Today's Events", value: data?.today_events ?? 0, sub: data?.calendar_conflicts ? `${data.calendar_conflicts} conflicts` : "Nothing scheduled", href: "/calendar" },
    { key: "tasks", label: "Tasks", value: data?.task_stats.pending ?? 0, sub: overdueTasks > 0 ? `${overdueTasks} overdue · stay on top of them` : "All caught up", href: "/tasks" },
    { key: "docs", label: "Documents", value: data?.documents_count ?? 0, sub: "Notes & uploads", href: "/documents" },
  ];

  const name = displayName(data?.student_email);
  const deadlineThisWeek = data?.deadline_this_week ?? 0;
  const replyRequired = data?.email_stats.reply_required ?? 0;
  const inProgress = data?.task_stats.in_progress ?? 0;
  const summaryBits: string[] = [];
  if (deadlineThisWeek > 0) summaryBits.push(`${deadlineThisWeek} deadline${deadlineThisWeek > 1 ? "s" : ""} this week`);
  if (replyRequired > 0) summaryBits.push(`${replyRequired} email${replyRequired > 1 ? "s" : ""} needing a reply`);
  if (inProgress > 0) summaryBits.push(`${inProgress} task${inProgress > 1 ? "s" : ""} in progress`);
  const summary = summaryBits.length > 0 ? `You have ${summaryBits.join(" · ")}.` : "Nothing urgent on the horizon — enjoy the calm.";

  return (
    <div className="space-y-8">
      {/* Hero greeting */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-pink-600 via-fuchsia-600 to-indigo-600 text-white p-8">
        <div className="absolute -right-16 -top-16 w-64 h-64 rounded-full bg-white/10" />
        <div className="absolute right-24 top-10 w-32 h-32 rounded-full bg-white/5" />
        <div className="relative flex flex-col md:flex-row md:items-center md:justify-between gap-6">
          <div>
            <div className="flex items-center gap-2 text-pink-100 text-sm font-medium mb-2">
              <Sparkles className="w-4 h-4" />
              <span>{new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}</span>
            </div>
            <h1 className="text-3xl md:text-4xl font-bold">
              {greeting()}{name ? `, ${name}` : ""}!
            </h1>
            <p className="mt-2 text-pink-50/90 max-w-xl text-sm md:text-base">{summary}</p>
            {data?.student_email && (
              <p className="mt-3 text-pink-100/70 text-xs">{data.student_email}</p>
            )}
          </div>
          <div className="flex flex-col sm:flex-row gap-3 shrink-0">
            <Link href="/briefing" className="bg-white text-pink-700 font-semibold rounded-xl px-5 py-2.5 text-sm inline-flex items-center justify-center gap-2 hover:bg-pink-50 transition-colors">
              <MessageSquare className="w-4 h-4" /> Morning Briefing
            </Link>
            <Link href="/tasks" className="bg-white/15 backdrop-blur text-white font-medium rounded-xl px-5 py-2.5 text-sm inline-flex items-center justify-center gap-2 hover:bg-white/25 transition-colors">
              <Send className="w-4 h-4" /> Plan Tasks
            </Link>
          </div>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
        {stats.map(({ key, label, value, sub, href }) => {
          const meta = STAT_META.find((s) => s.key === key)!;
          const Icon = meta.icon;
          return (
            <Link key={key} href={href} className="card card-hover group">
              <div className="flex items-start justify-between mb-3">
                <span className="stat-label">{label}</span>
                <span className={`w-9 h-9 rounded-xl flex items-center justify-center ${meta.chip}`}>
                  <Icon className="w-5 h-5" />
                </span>
              </div>
              <div className="stat-value text-slate-900">{value}</div>
              <p className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                {sub}
                <ArrowRight className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
              </p>
            </Link>
          );
        })}
      </div>

      {data && data.calendar_conflicts > 0 && (
        <div className="card border-amber-200 bg-amber-50">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-600" />
            <div>
              <p className="text-sm font-medium text-amber-600">
                {data.calendar_conflicts} calendar conflict{data.calendar_conflicts > 1 ? "s" : ""} detected
              </p>
              <p className="text-xs text-amber-600/70 mt-0.5">Go to Calendar to review suggested resolutions</p>
            </div>
          </div>
        </div>
      )}

      {data?.google_auth && !data.google_auth.connected && (
        <div className="card border-red-200 bg-red-50">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-red-600 shrink-0" />
            <div className="flex-1">
              <p className="text-sm font-medium text-red-700">
                Google connection needs your attention
              </p>
              <p className="text-xs text-red-600/80 mt-0.5">
                {data.google_auth.reason === "not_authorized"
                  ? "No Google account is connected yet. Gmail, Classroom and Calendar won't sync until you connect one."
                  : "Your Google authorization expired and can no longer be refreshed. Re-authorize to restore syncing."}
              </p>
            </div>
            <Link href="/emails" className="btn-primary text-xs shrink-0">
              Fix in Email Settings
            </Link>
          </div>
        </div>
      )}

      {/* Deadlines + attention */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Clock className="w-5 h-5 text-pink-600" />
              <h2 className="text-lg font-semibold text-slate-900">Next Deadlines</h2>
            </div>
            <Link href="/tasks" className="text-xs text-pink-600 hover:text-pink-700 font-medium">
              View all
            </Link>
          </div>
          {data && data.upcoming_deadlines && data.upcoming_deadlines.length > 0 ? (
            <div className="divide-y divide-slate-100">
              {data.upcoming_deadlines.map((d, i) => {
                const { label, overdue, near } = fmtDue(d.due_at, d.overdue);
                return (
                  <div key={i} className="flex items-center gap-3 py-3">
                    <span className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${overdue ? "bg-red-100 text-red-600" : near ? "bg-amber-100 text-amber-600" : "bg-slate-100 text-slate-500"}`}>
                      <Clock className="w-4 h-4" />
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">{d.title}</p>
                      {d.course && <p className="text-xs text-slate-500 truncate">{d.course}</p>}
                    </div>
                    <div className="text-right shrink-0">
                      <span className={`text-xs font-semibold ${overdue ? "text-red-600" : "text-slate-600"}`}>{label}</span>
                      <p className="text-[11px] text-slate-400 capitalize">{d.kind}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="py-8 text-center">
              <CheckSquare className="w-8 h-8 text-emerald-400 mx-auto mb-2" />
              <p className="text-sm text-slate-600">No upcoming deadlines — you're all caught up.</p>
            </div>
          )}
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Inbox className="w-5 h-5 text-blue-600" />
              <h2 className="text-lg font-semibold text-slate-900">Needs Your Attention</h2>
            </div>
            <Link href="/emails" className="text-xs text-pink-600 hover:text-pink-700 font-medium">
              Open inbox
            </Link>
          </div>
          {data && data.important_emails && data.important_emails.length > 0 ? (
            <div className="divide-y divide-slate-100">
              {data.important_emails.map((e) => (
                <Link key={e.id} href="/emails" className="flex items-center gap-3 py-3 group">
                  <span className="w-10 h-10 rounded-xl bg-blue-100 text-blue-600 flex items-center justify-center shrink-0">
                    <Mail className="w-4 h-4" />
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate group-hover:text-pink-600 transition-colors">{e.subject}</p>
                    <p className="text-xs text-slate-500 truncate">{e.sender || "Unknown sender"}</p>
                  </div>
                  <span className="text-[11px] font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5 shrink-0 capitalize">
                    {e.category.replace("_", " ")}
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center">
              <Inbox className="w-8 h-8 text-emerald-400 mx-auto mb-2" />
              <p className="text-sm text-slate-600">Inbox is clear — nothing needs you right now.</p>
            </div>
          )}
        </div>
      </div>

      {/* Quick actions + News */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 md:gap-6">
        <div className="card h-fit">
          <div className="flex items-center gap-2 mb-4">
            <MessageSquare className="w-5 h-5 text-pink-600" />
            <h2 className="text-lg font-semibold text-slate-900">Quick Actions</h2>
          </div>
          <div className="flex flex-col sm:flex-row lg:flex-col gap-3">
            <Link href="/briefing" className="btn-primary text-center">Morning Briefing</Link>
            <Link href="/tasks" className="btn-secondary text-center">View Tasks</Link>
            <Link href="/documents" className="btn-secondary text-center">Upload Document</Link>
          </div>
        </div>

        {news.length > 0 && (
          <div className="card lg:col-span-2">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Newspaper className="w-5 h-5 text-pink-600" />
                <h2 className="text-lg font-semibold text-slate-900">Top Technology News</h2>
              </div>
              <Link href="/news" className="text-xs text-pink-600 hover:text-pink-700 font-medium">
                View all →
              </Link>
            </div>
            <div className="space-y-3">
              {news.map((item, i) => (
                <a key={i} href={item.link} target="_blank" rel="noopener noreferrer" className="block group">
                  <div className="flex items-start gap-3">
                    <span className="text-xs font-bold text-slate-500 mt-1 w-5">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-medium text-slate-800 group-hover:text-pink-600 transition-colors">{item.title}</h3>
                      <p className="text-xs text-slate-500 mt-0.5 truncate">{item.source}</p>
                    </div>
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
