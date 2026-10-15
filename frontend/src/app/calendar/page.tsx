"use client";

import { useEffect, useState } from "react";
import { api, Event, FreeSlot } from "@/lib/api";
import { Calendar, Clock, AlertTriangle, Plus, Trash2, Zap, Repeat, BookOpen, CloudUpload } from "lucide-react";
import BackButton from "@/components/BackButton";

const RECURRENCE_OPTIONS = [
  { value: "", label: "No repeat" },
  { value: "daily", label: "Every day" },
  { value: "weekdays", label: "Weekdays" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

export default function CalendarPage() {
  const today = new Date().toISOString().split("T")[0];
  const [events, setEvents] = useState<Event[]>([]);
  const [freeSlots, setFreeSlots] = useState<FreeSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [date, setDate] = useState(today);
  const [showCreate, setShowCreate] = useState(false);
  const [newEvent, setNewEvent] = useState({ title: "", start_time: "", end_time: "", recurrence: "" });
  const [conflicts, setConflicts] = useState<Array<{ event1: string; event2: string; description: string; suggestion: string }>>([]);
  const [error, setError] = useState<string | null>(null);
  const [showStudy, setShowStudy] = useState(false);
  const [showSync, setShowSync] = useState(false);
  const [busy, setBusy] = useState(false);
  const [study, setStudy] = useState({ minutes: "60", subject: "", days: "7", overwrite: false });
  const [planOverwrite, setPlanOverwrite] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [e, slots] = await Promise.all([
        api.events.list({ date_from: date, date_to: date }),
        api.events.freeSlots(date),
      ]);
      setEvents(e);
      setFreeSlots(slots.free_slots);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load calendar");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [date]);

  const detectConflicts = async () => {
    const result = await api.events.detectConflicts({ date_from: date, date_to: date });
    setConflicts(result);
    await load();
  };

  const createEvent = async () => {
    if (!newEvent.title || !newEvent.start_time || !newEvent.end_time) return;
    await api.events.create({
      title: newEvent.title,
      start_time: new Date(`${date}T${newEvent.start_time}`).toISOString(),
      end_time: new Date(`${date}T${newEvent.end_time}`).toISOString(),
      recurrence_rule: newEvent.recurrence || null,
    });
    setNewEvent({ title: "", start_time: "", end_time: "", recurrence: "" });
    setShowCreate(false);
    await load();
  };

  const deleteEvent = async (id: number) => {
    await api.events.delete(id);
    await load();
  };

  const scheduleStudy = async () => {
    setBusy(true);
    setError(null);
    try {
      const from = new Date();
      const to = new Date();
      to.setDate(to.getDate() + (parseInt(study.days) || 1) - 1);
      const res = await api.events.scheduleStudy(
        from.toISOString().split("T")[0],
        to.toISOString().split("T")[0],
        parseInt(study.minutes) || 60,
        study.subject || undefined,
        study.overwrite
      );
      setShowStudy(false);
      await load();
      setError(`Scheduled ${res.created} study block${res.created === 1 ? "" : "s"} across the next ${study.days} day${study.days === "1" ? "" : "s"}.`);
    } catch (err: any) {
      setError(err.message || "Failed to schedule study time");
    } finally {
      setBusy(false);
    }
  };

  const syncToGoogle = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.events.syncToGoogle(date, date);
      setShowSync(false);
      await load();
      setError(`Google Calendar sync: ${res.created} created, ${res.skipped} skipped, ${res.failed} failed.`);
    } catch (err: any) {
      setError(err.message || "Failed to sync to Google Calendar");
    } finally {
      setBusy(false);
    }
  };

  const planExams = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.events.planExams(3, 120, planOverwrite);
      await load();
      setError(res.message || `Created ${res.events_created} calendar study block${res.events_created === 1 ? "" : "s"} and ${res.tasks_created} preparation task${res.tasks_created === 1 ? "" : "s"}.`);
    } catch (err: any) {
      setError(err.message || "Failed to generate exam preparation plan");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      {error && (
        <div className="card border-amber-200 bg-amber-50 flex items-center gap-3">
          <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
          <p className="text-sm text-amber-700 flex-1">{error}</p>
          <button onClick={() => window.location.reload()} className="btn-secondary text-xs">Retry</button>
        </div>
      )}
      <div className="flex items-center justify-between">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3">Calendar Intelligence</h1>
          <p className="text-slate-500 mt-1">{events.length} events on this day</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={detectConflicts} className="btn-secondary flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" /> Detect Conflicts
          </button>
          <button onClick={() => setShowStudy(!showStudy)} className="btn-secondary flex items-center gap-1.5">
            <BookOpen className="w-4 h-4" /> Schedule Study
          </button>
          <button onClick={planExams} disabled={busy} className="btn-secondary flex items-center gap-1.5">
            <Zap className="w-4 h-4" /> {busy ? "Planning…" : "Plan Exams"}
          </button>
          <button onClick={() => setShowSync(!showSync)} className="btn-secondary flex items-center gap-1.5">
            <CloudUpload className="w-4 h-4" /> Sync to Google
          </button>
          <button onClick={() => setShowCreate(!showCreate)} className="btn-primary flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Add Event
          </button>
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-500">
          <input type="checkbox" checked={planOverwrite} onChange={(e) => setPlanOverwrite(e.target.checked)} />
          Replace previously generated exam plan
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="input w-auto" />
      </div>

      {showStudy && (
        <div className="card border-emerald-200 space-y-3">
          <h3 className="text-sm font-semibold text-emerald-700 flex items-center gap-2">
            <BookOpen className="w-4 h-4" /> Auto-schedule study time into free slots
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500">Minutes / day</label>
              <input type="number" className="input mt-1" value={study.minutes} onChange={(e) => setStudy({ ...study, minutes: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Days ahead</label>
              <input type="number" className="input mt-1" value={study.days} onChange={(e) => setStudy({ ...study, days: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Subject (optional)</label>
              <input className="input mt-1" value={study.subject} onChange={(e) => setStudy({ ...study, subject: e.target.value })} placeholder="e.g. Data Structures" />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" checked={study.overwrite} onChange={(e) => setStudy({ ...study, overwrite: e.target.checked })} />
                Replace existing study blocks
              </label>
            </div>
          </div>
          <button onClick={scheduleStudy} disabled={busy} className="btn-primary w-full">
            {busy ? "Scheduling…" : "Schedule study time"}
          </button>
        </div>
      )}

      {showSync && (
        <div className="card border-sky-200 space-y-3">
          <h3 className="text-sm font-semibold text-sky-700 flex items-center gap-2">
            <CloudUpload className="w-4 h-4" /> Push events to your real Google Calendar
          </h3>
          <p className="text-xs text-slate-500">
            Copies the events shown for {date} into your Google Calendar. Requires a connected Google account with the Calendar scope.
          </p>
          <button onClick={syncToGoogle} disabled={busy} className="btn-primary w-full">
            {busy ? "Syncing…" : `Sync ${date} to Google Calendar`}
          </button>
        </div>
      )}

      {showCreate && (
        <div className="card border-pink-200 space-y-3">
          <h3 className="text-sm font-semibold text-slate-800">New Event</h3>
          <input
            placeholder="Event title"
            value={newEvent.title}
            onChange={(e) => setNewEvent({ ...newEvent, title: e.target.value })}
            className="input"
          />
          <div className="grid grid-cols-2 gap-2">
            <input type="time" value={newEvent.start_time} onChange={(e) => setNewEvent({ ...newEvent, start_time: e.target.value })} className="input" />
            <input type="time" value={newEvent.end_time} onChange={(e) => setNewEvent({ ...newEvent, end_time: e.target.value })} className="input" />
          </div>
          <select
            value={newEvent.recurrence}
            onChange={(e) => setNewEvent({ ...newEvent, recurrence: e.target.value })}
            className="input"
          >
            {RECURRENCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button onClick={createEvent} className="btn-primary w-full">Create Event</button>
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-amber-600 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> Detected Conflicts
          </h3>
          {conflicts.map((c, i) => (
            <div key={i} className="card border-amber-200 bg-amber-50">
              <p className="text-sm text-amber-600 font-medium">{c.event1} ↔ {c.event2}</p>
              <p className="text-xs text-amber-600/70 mt-1">{c.description}</p>
              <p className="text-xs text-amber-600/70 mt-1">Suggestion: {c.suggestion}</p>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-600 flex items-center gap-2">
              <Calendar className="w-4 h-4" /> Events
            </h3>
            {events.map((event) => (
              <div key={`${event.id}-${event.start_time}`} className={`card flex items-start justify-between ${event.is_conflict ? "border-amber-200" : ""}`}>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-800">{event.title}</span>
                    {event.is_conflict && <AlertTriangle className="w-3.5 h-3.5 text-amber-600" />}
                    {event.recurrence_rule && (
                      <span className="flex items-center gap-1 badge bg-pink-50 text-pink-600">
                        <Repeat className="w-3 h-3" /> {event.recurrence_rule}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-slate-500 mt-1">
                    <Clock className="w-3 h-3" />
                    {new Date(event.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} - {new Date(event.end_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </div>
                  {event.conflict_description && (
                    <p className="text-xs text-amber-600/70 mt-1">{event.conflict_description}</p>
                  )}
                  {event.suggested_action && (
                    <p className="text-xs text-pink-600 mt-1">Suggestion: {event.suggested_action}</p>
                  )}
                </div>
                <button onClick={() => deleteEvent(event.id)} className="btn-ghost p-1 text-slate-500 hover:text-red-600">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
            {events.length === 0 && <p className="text-sm text-slate-500 py-8 text-center">No events</p>}
          </div>

          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-emerald-600 flex items-center gap-2">
              <Zap className="w-4 h-4" /> Free Slots
            </h3>
            {freeSlots.map((slot, i) => (
              <div key={i} className="card border-emerald-200">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-emerald-600">
                    {slot.start} - {slot.end}
                  </span>
                  <span className="badge bg-emerald-50 text-emerald-600">{slot.duration_minutes} min</span>
                </div>
              </div>
            ))}
            {freeSlots.length === 0 && <p className="text-sm text-slate-500 py-8 text-center">No free slots found</p>}
          </div>
        </div>
      )}
    </div>
  );
}
