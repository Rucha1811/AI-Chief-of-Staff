"use client";

import { useEffect, useState } from "react";
import { api, Dashboard as DashboardType } from "@/lib/api";
import {
  Mail,
  Calendar,
  CheckSquare,
  FileText,
  AlertTriangle,
  MessageSquare,
} from "lucide-react";
import Link from "next/link";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardType | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.dashboard.get().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500" />
      </div>
    );
  }

  const stats = [
    {
      label: "Emails",
      value: data?.email_stats.total ?? 0,
      sub: `${data?.email_stats.unread ?? 0} unread · ${data?.email_stats.reply_required ?? 0} need reply`,
      icon: Mail,
      color: "text-blue-400",
      href: "/emails",
    },
    {
      label: "Today's Events",
      value: data?.today_events ?? 0,
      sub: data?.calendar_conflicts ? `${data.calendar_conflicts} conflicts` : "No conflicts",
      icon: Calendar,
      color: "text-emerald-400",
      href: "/calendar",
    },
    {
      label: "Tasks",
      value: data?.task_stats.pending ?? 0,
      sub: `${data?.task_stats.in_progress ?? 0} in progress · ${data?.task_stats.overdue ?? 0} overdue`,
      icon: CheckSquare,
      color: "text-amber-400",
      href: "/tasks",
    },
    {
      label: "Documents",
      value: data?.documents_count ?? 0,
      sub: "Uploaded documents",
      icon: FileText,
      color: "text-purple-400",
      href: "/documents",
    },
  ];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Dashboard</h1>
        <p className="text-slate-400 mt-1">
          Your AI-powered command center. {new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map(({ label, value, sub, icon: Icon, color, href }) => (
          <Link key={label} href={href} className="card hover:bg-slate-800/70 transition-colors">
            <div className="flex items-start justify-between mb-3">
              <span className="stat-label">{label}</span>
              <Icon className={`w-5 h-5 ${color}`} />
            </div>
            <div className="stat-value text-slate-100">{value}</div>
            <p className="text-xs text-slate-500 mt-1">{sub}</p>
          </Link>
        ))}
      </div>

      {data && data.calendar_conflicts > 0 && (
        <div className="card border-amber-700/50 bg-amber-900/10">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400" />
            <div>
              <p className="text-sm font-medium text-amber-300">
                {data.calendar_conflicts} calendar conflict{data.calendar_conflicts > 1 ? "s" : ""} detected
              </p>
              <p className="text-xs text-amber-400/70 mt-0.5">
                Go to Calendar to review suggested resolutions
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="flex items-center gap-2 mb-4">
          <MessageSquare className="w-5 h-5 text-indigo-400" />
          <h2 className="text-lg font-semibold text-slate-100">Quick Actions</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <Link href="/briefing" className="btn-primary text-center">
            Morning Briefing
          </Link>
          <Link href="/tasks" className="btn-secondary text-center">
            View Tasks
          </Link>
          <Link href="/documents" className="btn-secondary text-center">
            Upload Document
          </Link>
        </div>
      </div>
    </div>
  );
}
