"use client";

import { useEffect, useState } from "react";
import { api, TimetableEntry, ClassroomAssignment } from "@/lib/api";
import {
  Calendar,
  Clock,
  Plus,
  Trash2,
  Zap,
  GraduationCap,
  MessageSquare,
  ExternalLink,
  RefreshCw,
  School,
} from "lucide-react";
import BackButton from "@/components/BackButton";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function TimetablePage() {
  const [entries, setEntries] = useState<TimetableEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ title: "", location: "", day_of_week: 0, start_time: "09:00", end_time: "10:00", source: "college" });
  const [scheduleMsg, setScheduleMsg] = useState<string | null>(null);
  const [scheduling, setScheduling] = useState(false);

  const [classStatus, setClassStatus] = useState<{ connected: boolean; reason: string } | null>(null);
  const [classAssignments, setClassAssignments] = useState<ClassroomAssignment[] | null>(null);
  const [classMsg, setClassMsg] = useState<string | null>(null);
  const [classBusy, setClassBusy] = useState(false);

  const [teamsStatus, setTeamsStatus] = useState<{ configured: boolean; connected: boolean; account?: string; reason: string } | null>(null);
  const [teamsFlow, setTeamsFlow] = useState<{ user_code: string; verification_uri: string; message: string } | null>(null);
  const [teamsMsg, setTeamsMsg] = useState<string | null>(null);
  const [teamsBusy, setTeamsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setEntries(await api.timetable.list());
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load timetable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => { api.classroom.status().then(setClassStatus).catch(() => setClassStatus({ connected: false, reason: "API unreachable" })); }, []);
  useEffect(() => { api.teams.status().then(setTeamsStatus).catch(() => setTeamsStatus({ configured: false, connected: false, reason: "API unreachable" })); }, []);

  const addEntry = async () => {
    if (!form.title) return;
    await api.timetable.create(form);
    setForm({ title: "", location: "", day_of_week: 0, start_time: "09:00", end_time: "10:00", source: "college" });
    setShowAdd(false);
    await load();
  };

  const deleteEntry = async (id: number) => {
    await api.timetable.delete(id);
    await load();
  };

  const scheduleWeek = async () => {
    setScheduling(true);
    setScheduleMsg(null);
    try {
      const today = new Date();
      const to = new Date(today);
      to.setDate(to.getDate() + 6);
      const res = await api.timetable.schedule(
        today.toISOString().split("T")[0],
        to.toISOString().split("T")[0],
        true
      );
      setScheduleMsg(`Scheduled ${res.created} classes on your calendar (${res.skipped} skipped).`);
    } finally {
      setScheduling(false);
    }
  };

  const syncClassroom = async () => {
    setClassBusy(true);
    setClassMsg(null);
    try {
      const res = await api.classroom.sync();
      setClassMsg(`Synced ${res.assignments} assignments → ${res.tasks_created} tasks, ${res.events_created} calendar events.`);
      setClassAssignments(await api.classroom.assignments());
    } catch (e: any) {
      setClassMsg(e.message);
    } finally {
      setClassBusy(false);
    }
  };

  const loadAssignments = async () => {
    setClassBusy(true);
    try {
      setClassAssignments(await api.classroom.assignments());
    } catch (e: any) {
      setClassMsg(e.message);
    } finally {
      setClassBusy(false);
    }
  };

  const connectTeams = async () => {
    setTeamsBusy(true);
    setTeamsMsg(null);
    try {
      const res = await api.teams.connect();
      setTeamsFlow({ user_code: res.user_code, verification_uri: res.verification_uri, message: res.message });
      const interval = setInterval(async () => {
        try {
          const poll = await api.teams.poll();
          if (poll.status === "success") {
            clearInterval(interval);
            setTeamsFlow(null);
            setTeamsMsg(`Connected as ${poll.account}.`);
            setTeamsStatus(await api.teams.status());
          } else if (poll.status === "expired") {
            clearInterval(interval);
            setTeamsFlow(null);
            setTeamsMsg("Sign-in link expired. Try again.");
          }
        } catch {
          clearInterval(interval);
        }
      }, 5000);
    } catch (e: any) {
      setTeamsMsg(e.message);
    } finally {
      setTeamsBusy(false);
    }
  };

  const syncTeams = async () => {
    setTeamsBusy(true);
    setTeamsMsg(null);
    try {
      const res = await api.teams.sync();
      setTeamsMsg(`Synced ${res.fetched} meetings → ${res.created} added to calendar.`);
    } catch (e: any) {
      setTeamsMsg(e.message);
    } finally {
      setTeamsBusy(false);
    }
  };

  const grouped = DAYS.map((_, i) => entries.filter((e) => e.day_of_week === i));

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
          <h1 className="text-2xl font-bold text-gradient mt-3">Timetable &amp; Scheduling</h1>
          <p className="text-slate-500 mt-1">Your weekly classes, auto-scheduled on the calendar</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={scheduleWeek} disabled={scheduling} className="btn-primary flex items-center gap-1.5">
            <Zap className={`w-4 h-4 ${scheduling ? "animate-pulse" : ""}`} />
            Schedule on Calendar
          </button>
          <button onClick={() => setShowAdd(!showAdd)} className="btn-secondary flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Add Class
          </button>
        </div>
      </div>

      {scheduleMsg && <div className="card border-emerald-200 bg-emerald-50 text-sm text-emerald-700">{scheduleMsg}</div>}

      {showAdd && (
        <div className="card border-pink-200 space-y-3">
          <h3 className="text-sm font-semibold text-slate-800">New Timetable Entry</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <input placeholder="Class name (e.g. Data Structures)" value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })} className="input" />
            <input placeholder="Location / Room / Link" value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })} className="input" />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            <select value={form.day_of_week} onChange={(e) => setForm({ ...form, day_of_week: Number(e.target.value) })} className="input">
              {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
            <input type="time" value={form.start_time} onChange={(e) => setForm({ ...form, start_time: e.target.value })} className="input" />
            <input type="time" value={form.end_time} onChange={(e) => setForm({ ...form, end_time: e.target.value })} className="input" />
            <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} className="input">
              <option value="college">College</option>
              <option value="teams">Microsoft Teams</option>
              <option value="classroom">Google Classroom</option>
              <option value="manual">Other</option>
            </select>
            <button onClick={addEntry} className="btn-primary">Add</button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-slate-600 flex items-center gap-2">
            <GraduationCap className="w-4 h-4" /> Weekly Timetable
          </h3>
          {loading ? (
            <div className="flex justify-center py-10">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
            </div>
          ) : entries.length === 0 ? (
            <div className="card">
              <p className="text-sm text-slate-500 text-center py-8">
                No timetable yet. Add your classes above, then hit "Schedule on Calendar".
              </p>
            </div>
          ) : (
            DAYS.map((day, i) => {
              const dayEntries = grouped[i];
              if (dayEntries.length === 0) return null;
              return (
                <div key={day} className="card">
                  <h4 className="text-xs font-semibold text-pink-600 mb-2">{day}</h4>
                  <div className="space-y-1.5">
                    {dayEntries.map((e) => (
                      <div key={e.id} className="flex items-center justify-between group">
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-slate-800">{e.title}</span>
                            <span className="badge bg-slate-100 text-slate-500">{e.source}</span>
                          </div>
                          <div className="flex items-center gap-2 text-xs text-slate-500 mt-0.5">
                            <Clock className="w-3 h-3" />
                            {e.start_time} - {e.end_time}
                            {e.location && <span className="truncate">· {e.location}</span>}
                          </div>
                        </div>
                        <button onClick={() => deleteEntry(e.id)} className="btn-ghost p-1 text-slate-500 hover:text-red-600">
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="space-y-4">
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                <School className="w-4 h-4 text-blue-600" /> Google Classroom
              </h3>
              <span className={`badge ${classStatus?.connected ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"}`}>
                {classStatus?.connected ? "Connected" : "Not connected"}
              </span>
            </div>
            {!classStatus?.connected ? (
              <p className="text-xs text-slate-500 leading-relaxed">
                {classStatus?.reason} To enable it, re-authorize your Google account
                with the Classroom scope, then sync your courses and assignments here.
              </p>
            ) : (
              <div className="space-y-2">
                <button onClick={syncClassroom} disabled={classBusy} className="btn-secondary w-full flex items-center justify-center gap-1.5">
                  <RefreshCw className={`w-4 h-4 ${classBusy ? "animate-spin" : ""}`} /> Sync Assignments
                </button>
                <button onClick={loadAssignments} disabled={classBusy} className="btn-ghost w-full text-xs">
                  View upcoming assignments
                </button>
              </div>
            )}
            {classMsg && <p className="text-xs mt-2 text-emerald-600">{classMsg}</p>}
            {classAssignments && classAssignments.length > 0 && (
              <div className="mt-3 space-y-1.5 max-h-56 overflow-y-auto scrollbar-thin">
                {classAssignments.slice(0, 10).map((a) => (
                  <div key={a.id} className="border-b border-slate-100 pb-1.5 last:border-0">
                    <p className="text-xs font-medium text-slate-800">{a.title}</p>
                    <p className="text-[11px] text-slate-500">{a.course_name} {a.due ? `· due ${new Date(a.due).toLocaleDateString()}` : ""}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-indigo-600" /> Microsoft Teams / Outlook
              </h3>
              <span className={`badge ${teamsStatus?.connected ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"}`}>
                {teamsStatus?.connected ? "Connected" : teamsStatus?.configured ? "Not connected" : "Not configured"}
              </span>
            </div>
            {!teamsStatus?.configured ? (
              <p className="text-xs text-slate-500 leading-relaxed">
                Add your Azure app client ID as <code className="text-pink-600">MS_CLIENT_ID</code> in a
                <code className="text-pink-600"> .env</code> file (see backend .env.example), then connect.
              </p>
            ) : !teamsStatus?.connected && !teamsFlow ? (
              <button onClick={connectTeams} disabled={teamsBusy} className="btn-secondary w-full flex items-center justify-center gap-1.5">
                Connect Microsoft Account
              </button>
            ) : teamsFlow ? (
              <div className="space-y-2">
                <div className="rounded-lg bg-indigo-50 border border-indigo-100 p-3 text-center">
                  <div className="text-3xl font-bold text-indigo-700 tracking-widest">{teamsFlow.user_code}</div>
                  <p className="text-xs text-slate-500 mt-1">Enter this code at the sign-in page</p>
                </div>
                <a href={teamsFlow.verification_uri} target="_blank" rel="noreferrer"
                  className="btn-primary w-full flex items-center justify-center gap-1.5">
                  <ExternalLink className="w-4 h-4" /> Open sign-in page
                </a>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-emerald-600">{teamsStatus?.account || "Connected."}</p>
                <button onClick={syncTeams} disabled={teamsBusy} className="btn-secondary w-full flex items-center justify-center gap-1.5">
                  <RefreshCw className={`w-4 h-4 ${teamsBusy ? "animate-spin" : ""}`} /> Sync Meetings
                </button>
              </div>
            )}
            {teamsMsg && <p className="text-xs mt-2 text-emerald-600">{teamsMsg}</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
