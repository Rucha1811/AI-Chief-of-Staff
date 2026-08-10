"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Exam } from "@/lib/api";
import BackButton from "@/components/BackButton";
import { GraduationCap, Plus, Trash2, CalendarDays, Clock, MapPin } from "lucide-react";

export default function ExamsPage() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("09:00");
  const [duration, setDuration] = useState("180");
  const [location, setLocation] = useState("");
  const [notes, setNotes] = useState("");
  const [score, setScore] = useState("");
  const [maxScore, setMaxScore] = useState("100");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setExams(await api.exams.list());
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load exams");
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
      await api.exams.create({
        title,
        subject: subject || null,
        exam_date: `${date}T${time}`,
        duration_minutes: duration ? parseInt(duration) : null,
        location: location || null,
        notes: notes || null,
        status: "upcoming",
      });
      setTitle("");
      setSubject("");
      setLocation("");
      setNotes("");
      setShowForm(false);
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to add exam");
    }
  };

  const complete = async (exam: Exam) => {
    try {
      await api.exams.update(exam.id, {
        status: "completed",
        score: score ? parseInt(score) : exam.score,
        max_score: maxScore ? parseInt(maxScore) : exam.max_score,
      });
      setScore("");
      setMaxScore("100");
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to update exam");
    }
  };

  const saveScore = async (exam: Exam, value: string) => {
    try {
      await api.exams.update(exam.id, { score: value ? parseInt(value) : null });
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to save score");
    }
  };

  const remove = async (id: number) => {
    try {
      await api.exams.delete(id);
      await load();
    } catch (err: any) {
      setError(err.message || "Failed to delete exam");
    }
  };

  const fmtDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  };

  const upcoming = exams.filter((e) => e.status === "upcoming");
  const completed = exams.filter((e) => e.status === "completed");

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
          <h1 className="text-2xl font-bold text-gradient mt-3">Exam Schedule</h1>
          <p className="text-slate-500 mt-1">{upcoming.length} upcoming · {completed.length} completed</p>
        </div>
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary flex items-center gap-2">
          <Plus className="w-4 h-4" /> Add exam
        </button>
      </div>

      {showForm && (
        <form onSubmit={create} className="card space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-slate-500">Title *</label>
              <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="e.g. Data Structures Final" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Subject / Course</label>
              <input className="input mt-1" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. Computer Science" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Date *</label>
              <input type="date" className="input mt-1" value={date} onChange={(e) => setDate(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Start time</label>
              <input type="time" className="input mt-1" value={time} onChange={(e) => setTime(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Duration (min)</label>
              <input type="number" className="input mt-1" value={duration} onChange={(e) => setDuration(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Location</label>
              <input className="input mt-1" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. Hall B" />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500">Notes</label>
            <textarea className="input mt-1" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Units covered, allowed materials…" />
          </div>
          <div className="flex gap-3">
            <button type="submit" className="btn-primary">Add exam</button>
            <button type="button" onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : exams.length === 0 ? (
        <div className="card text-center py-16">
          <GraduationCap className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500">No exams yet. Add one to get reminders before the big day.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {exams.map((exam) => (
            <div key={exam.id} className={`card card-hover ${exam.status === "completed" ? "opacity-60" : ""}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 rounded-xl bg-pink-50">
                    <GraduationCap className="w-5 h-5 text-pink-500" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-800">{exam.title}</h3>
                    {exam.subject && <p className="text-xs text-slate-500">{exam.subject}</p>}
                  </div>
                </div>
                <button onClick={() => remove(exam.id)} className="text-slate-300 hover:text-red-500 transition-colors" aria-label="Delete">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="flex flex-wrap gap-4 mt-4 text-sm text-slate-500">
                <span className="flex items-center gap-1.5"><CalendarDays className="w-4 h-4" /> {fmtDate(exam.exam_date)} · {new Date(exam.exam_date).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                {exam.duration_minutes && <span className="flex items-center gap-1.5"><Clock className="w-4 h-4" /> {exam.duration_minutes}m</span>}
                {exam.location && <span className="flex items-center gap-1.5"><MapPin className="w-4 h-4" /> {exam.location}</span>}
              </div>
              {exam.notes && <p className="text-sm text-slate-500 mt-3">{exam.notes}</p>}
              <div className="mt-4 pt-4 border-t border-slate-100 flex items-center justify-between gap-3">
                {exam.status === "upcoming" ? (
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={maxScore}
                      onChange={(e) => setMaxScore(e.target.value)}
                      className="input !w-20 !py-1.5 text-sm"
                      placeholder="Max"
                    />
                    <input
                      type="number"
                      value={score}
                      onChange={(e) => setScore(e.target.value)}
                      className="input !w-20 !py-1.5 text-sm"
                      placeholder="Score"
                    />
                    <button onClick={() => complete(exam)} className="btn-secondary !py-1.5 text-xs">Complete</button>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <span className="badge bg-emerald-100 text-emerald-700">
                      {exam.score !== null && exam.score !== undefined ? `${exam.score}/${exam.max_score ?? "–"}` : "Completed"}
                    </span>
                    <input
                      type="number"
                      className="input !w-20 !py-1 text-sm"
                      placeholder="Score"
                      defaultValue={exam.score ?? ""}
                      onBlur={(e) => {
                        if (e.target.value && e.target.value !== String(exam.score ?? "")) {
                          saveScore(exam, e.target.value);
                        }
                      }}
                    />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
