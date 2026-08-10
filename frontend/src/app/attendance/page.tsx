"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type AttendanceRecord, type AttendanceStats } from "@/lib/api";
import BackButton from "@/components/BackButton";
import { UserCheck, Plus, Trash2 } from "lucide-react";

const STATUS_LABEL: Record<string, string> = { present: "Present", absent: "Absent", late: "Late", excused: "Excused" };

export default function AttendancePage() {
  const [records, setRecords] = useState<AttendanceRecord[]>([]);
  const [stats, setStats] = useState<AttendanceStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [course, setCourse] = useState("");
  const [date, setDate] = useState("");
  const [status, setStatus] = useState("present");
  const [notes, setNotes] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, s] = await Promise.all([api.attendance.list(), api.attendance.stats()]);
      setRecords(r);
      setStats(s);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load attendance");
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
      await api.attendance.create({
        course,
        date: `${date}T09:00:00`,
        status,
        notes: notes || undefined,
      });
      setCourse("");
      setDate("");
      setNotes("");
      setShowForm(false);
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to add record");
    }
  };

  const remove = async (id: number) => {
    try {
      await api.attendance.delete(id);
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to delete record");
    }
  };

  const statBar = (count: number) =>
    stats && stats.total > 0 ? `${Math.round((count / stats.total) * 100)}%` : "0%";

  const courses = Array.from(new Set(records.map((r) => r.course)));

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
          <h1 className="text-2xl font-bold text-gradient mt-3">Attendance</h1>
          <p className="text-slate-500 mt-1">{records.length} records · {courses.length} courses</p>
        </div>
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary flex items-center gap-2">
          <Plus className="w-4 h-4" /> Add record
        </button>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="stat-card">
            <span className="stat-value">{stats.percentage}%</span>
            <span className="stat-label">Attendance rate</span>
          </div>
          {[
            ["Present", stats.present, "bg-emerald-500"],
            ["Absent", stats.absent, "bg-red-500"],
            ["Late", stats.late, "bg-amber-400"],
            ["Excused", stats.excused, "bg-sky-500"],
          ].map(([label, count, color]) => (
            <div key={label as string} className="stat-card">
              <span className="stat-value">{count as number}</span>
              <span className="stat-label flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${color}`} /> {label as string}
              </span>
            </div>
          ))}
        </div>
      )}

      {stats && stats.total > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Breakdown</h3>
          <div className="flex h-3 rounded-full overflow-hidden bg-slate-100">
            <div className="bg-emerald-500" style={{ width: statBar(stats.present) }} />
            <div className="bg-red-500" style={{ width: statBar(stats.absent) }} />
            <div className="bg-amber-400" style={{ width: statBar(stats.late) }} />
            <div className="bg-sky-500" style={{ width: statBar(stats.excused) }} />
          </div>
        </div>
      )}

      {showForm && (
        <form onSubmit={create} className="card space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="text-xs font-medium text-slate-500">Course *</label>
              <input className="input mt-1" list="course-list" value={course} onChange={(e) => setCourse(e.target.value)} required placeholder="e.g. Mathematics" />
              <datalist id="course-list">
                {courses.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Date *</label>
              <input type="date" className="input mt-1" value={date} onChange={(e) => setDate(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Status</label>
              <select className="input mt-1" value={status} onChange={(e) => setStatus(e.target.value)}>
                {Object.entries(STATUS_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Notes</label>
              <input className="input mt-1" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional" />
            </div>
          </div>
          <div className="flex gap-3">
            <button type="submit" className="btn-primary">Add record</button>
            <button type="button" onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : records.length === 0 ? (
        <div className="card text-center py-16">
          <UserCheck className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500">No attendance records yet. Add your first class entry.</p>
        </div>
      ) : (
        <div className="card divide-y divide-slate-100 p-0">
          {records.map((r) => (
            <div key={r.id} className="flex items-center gap-4 px-5 py-3.5">
              <span
                className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                  r.status === "present" ? "bg-emerald-500" : r.status === "absent" ? "bg-red-500" : r.status === "late" ? "bg-amber-400" : "bg-sky-500"
                }`}
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800">{r.course}</p>
                <p className="text-xs text-slate-500">
                  {new Date(r.date).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
                  {r.notes ? ` · ${r.notes}` : ""}
                </p>
              </div>
              <span className={`badge ${
                r.status === "present" ? "bg-emerald-100 text-emerald-700" : r.status === "absent" ? "bg-red-100 text-red-700" : r.status === "late" ? "bg-amber-100 text-amber-700" : "bg-sky-100 text-sky-700"
              }`}>
                {STATUS_LABEL[r.status]}
              </span>
              <button onClick={() => remove(r.id)} className="text-slate-300 hover:text-red-500 transition-colors" aria-label="Delete">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
