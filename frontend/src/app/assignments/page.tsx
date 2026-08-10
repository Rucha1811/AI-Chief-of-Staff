"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Assignment } from "@/lib/api";
import BackButton from "@/components/BackButton";
import { ClipboardList, Plus, Trash2, RefreshCw, ExternalLink, AlertTriangle } from "lucide-react";

const STATUS_ORDER = ["todo", "in_progress", "submitted", "graded"];
const STATUS_LABEL: Record<string, string> = {
  todo: "To do",
  in_progress: "In progress",
  submitted: "Submitted",
  graded: "Graded",
};
const STATUS_BADGE: Record<string, string> = {
  todo: "bg-slate-100 text-slate-600",
  in_progress: "bg-amber-100 text-amber-700",
  submitted: "bg-sky-100 text-sky-700",
  graded: "bg-emerald-100 text-emerald-700",
};

export default function AssignmentsPage() {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const [title, setTitle] = useState("");
  const [course, setCourse] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [pointsMax, setPointsMax] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setAssignments(await api.assignments.list());
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load assignments");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.assignments.create({
        title,
        course: course || null,
        due_date: dueDate ? `${dueDate}T23:59:00` : null,
        points_max: pointsMax ? parseInt(pointsMax) : null,
        status: "todo",
        priority: "medium",
      });
      setTitle("");
      setCourse("");
      setDueDate("");
      setPointsMax("");
      setShowForm(false);
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to add assignment");
    }
  };

  const setStatus = async (a: Assignment, status: string) => {
    try {
      await api.assignments.update(a.id, { status });
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to update assignment");
    }
  };

  const saveScore = async (a: Assignment, earned: string) => {
    try {
      await api.assignments.update(a.id, {
        points_earned: earned ? parseInt(earned) : null,
        status: earned ? "graded" : a.status,
      });
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to save grade");
    }
  };

  const remove = async (id: number) => {
    try {
      await api.assignments.delete(id);
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to delete assignment");
    }
  };

  const syncClassroom = async () => {
    setSyncing(true);
    try {
      const res = await api.assignments.syncClassroom();
      await load();
      setError(null);
      alert(`Synced ${res.fetched} Classroom assignments (${res.created} new).`);
    } catch (err: any) {
      setError(err.message || "Failed to sync Classroom");
    } finally {
      setSyncing(false);
    }
  };

  const done = assignments.filter((a) => a.status === "graded" || a.status === "submitted").length;
  const avg =
    assignments.length > 0
      ? Math.round(
          assignments.reduce((sum, a) => sum + (a.points_earned && a.points_max ? a.points_earned / a.points_max : 0), 0) /
            assignments.length *
            100
        )
      : 0;

  const sorted = [...assignments].sort((a, b) => {
    const si = STATUS_ORDER.indexOf(a.status);
    const sj = STATUS_ORDER.indexOf(b.status);
    if (si !== sj) return si - sj;
    if (!a.due_date) return 1;
    if (!b.due_date) return -1;
    return new Date(a.due_date).getTime() - new Date(b.due_date).getTime();
  });

  return (
    <div className="space-y-6">
      {error && (
        <div className="card border-amber-200 bg-amber-50 flex items-center gap-3">
          <p className="text-sm text-amber-700 flex-1">{error}</p>
          <button onClick={() => window.location.reload()} className="btn-secondary text-xs">Retry</button>
        </div>
      )}
      <div className="flex items-center justify-between">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3">Assignments &amp; Grades</h1>
          <p className="text-slate-500 mt-1">{done}/{assignments.length} done · {avg > 0 ? `${avg}% average` : "no grades yet"}</p>
        </div>
        <div className="flex gap-3">
          <button onClick={syncClassroom} disabled={syncing} className="btn-secondary flex items-center gap-2">
            <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} /> Pull from Classroom
          </button>
          <button onClick={() => setShowForm((s) => !s)} className="btn-primary flex items-center gap-2">
            <Plus className="w-4 h-4" /> Add
          </button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={create} className="card space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-slate-500">Title *</label>
              <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="e.g. OS Lab Report" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Course</label>
              <input className="input mt-1" value={course} onChange={(e) => setCourse(e.target.value)} placeholder="e.g. Operating Systems" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Due date</label>
              <input type="date" className="input mt-1" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Max points</label>
              <input type="number" className="input mt-1" value={pointsMax} onChange={(e) => setPointsMax(e.target.value)} placeholder="e.g. 20" />
            </div>
          </div>
          <div className="flex gap-3">
            <button type="submit" className="btn-primary">Add assignment</button>
            <button type="button" onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : sorted.length === 0 ? (
        <div className="card text-center py-16">
          <ClipboardList className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500">No assignments yet. Add one or pull them from Google Classroom.</p>
        </div>
      ) : (
        <div className="card divide-y divide-slate-100 p-0">
          {sorted.map((a) => {
            const overdue = a.due_date && a.status !== "graded" && a.status !== "submitted" && new Date(a.due_date).getTime() < Date.now();
            return (
              <div key={a.id} className="flex items-center gap-4 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium text-slate-800 truncate">{a.title}</h3>
                    {a.source === "classroom" && <span className="badge bg-violet-100 text-violet-700 shrink-0">Classroom</span>}
                    {overdue && (
                      <span className="flex items-center gap-1 badge bg-red-100 text-red-700 shrink-0">
                        <AlertTriangle className="w-3 h-3" /> Overdue
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {a.course || "General"} · {a.due_date ? `due ${new Date(a.due_date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : "no due date"}
                  </p>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {a.status === "graded" ? (
                    <div className="flex items-center gap-1.5">
                      <input
                        type="number"
                        className="input !w-16 !py-1 text-sm"
                        defaultValue={a.points_earned ?? ""}
                        placeholder={a.points_max ? `/${a.points_max}` : "score"}
                        onBlur={(e) => {
                          if (e.target.value) saveScore(a, e.target.value);
                        }}
                      />
                    </div>
                  ) : (
                    a.points_max != null && (
                      <span className="text-xs text-slate-400 hidden md:inline">/{a.points_max}</span>
                    )
                  )}
                  <select
                    value={a.status}
                    onChange={(e) => setStatus(a, e.target.value)}
                    className="input !w-auto !py-1.5 text-sm"
                  >
                    {STATUS_ORDER.map((s) => (
                      <option key={s} value={s}>{STATUS_LABEL[s]}</option>
                    ))}
                  </select>
                  {a.external_url && (
                    <a href={a.external_url} target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-pink-600" aria-label="Open">
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  )}
                  <button onClick={() => remove(a.id)} className="text-slate-300 hover:text-red-500 transition-colors" aria-label="Delete">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
