"use client";

import { useEffect, useState } from "react";
import { api, Event, FreeSlot } from "@/lib/api";
import { Calendar, Clock, AlertTriangle, Plus, Trash2, Zap } from "lucide-react";

export default function CalendarPage() {
  const today = new Date().toISOString().split("T")[0];
  const [events, setEvents] = useState<Event[]>([]);
  const [freeSlots, setFreeSlots] = useState<FreeSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [date, setDate] = useState(today);
  const [showCreate, setShowCreate] = useState(false);
  const [newEvent, setNewEvent] = useState({ title: "", start_time: "", end_time: "" });
  const [conflicts, setConflicts] = useState<Array<{ event1: string; event2: string; description: string; suggestion: string }>>([]);

  const load = async () => {
    setLoading(true);
    try {
      const [e, slots] = await Promise.all([
        api.events.list({ date_from: date, date_to: date }),
        api.events.freeSlots(date),
      ]);
      setEvents(e);
      setFreeSlots(slots.free_slots);
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
    });
    setNewEvent({ title: "", start_time: "", end_time: "" });
    setShowCreate(false);
    await load();
  };

  const deleteEvent = async (id: number) => {
    await api.events.delete(id);
    await load();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Calendar Intelligence</h1>
          <p className="text-slate-400 mt-1">{events.length} events on this day</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={detectConflicts} className="btn-secondary flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" /> Detect Conflicts
          </button>
          <button onClick={() => setShowCreate(!showCreate)} className="btn-primary flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Add Event
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="input w-auto" />
      </div>

      {showCreate && (
        <div className="card border-indigo-700/50 space-y-3">
          <h3 className="text-sm font-semibold text-slate-200">New Event</h3>
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
          <button onClick={createEvent} className="btn-primary w-full">Create Event</button>
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-amber-400 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> Detected Conflicts
          </h3>
          {conflicts.map((c, i) => (
            <div key={i} className="card border-amber-700/50 bg-amber-900/10">
              <p className="text-sm text-amber-300 font-medium">{c.event1} ↔ {c.event2}</p>
              <p className="text-xs text-amber-400/70 mt-1">{c.description}</p>
              <p className="text-xs text-amber-400/70 mt-1">Suggestion: {c.suggestion}</p>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500" />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
              <Calendar className="w-4 h-4" /> Events
            </h3>
            {events.map((event) => (
              <div key={event.id} className={`card flex items-start justify-between ${event.is_conflict ? "border-amber-700/50" : ""}`}>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-200">{event.title}</span>
                    {event.is_conflict && <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-slate-400 mt-1">
                    <Clock className="w-3 h-3" />
                    {new Date(event.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} - {new Date(event.end_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </div>
                  {event.conflict_description && (
                    <p className="text-xs text-amber-400/70 mt-1">{event.conflict_description}</p>
                  )}
                  {event.suggested_action && (
                    <p className="text-xs text-indigo-400 mt-1">Suggestion: {event.suggested_action}</p>
                  )}
                </div>
                <button onClick={() => deleteEvent(event.id)} className="btn-ghost p-1 text-slate-500 hover:text-red-400">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
            {events.length === 0 && <p className="text-sm text-slate-500 py-8 text-center">No events</p>}
          </div>

          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-emerald-400 flex items-center gap-2">
              <Zap className="w-4 h-4" /> Free Slots
            </h3>
            {freeSlots.map((slot, i) => (
              <div key={i} className="card border-emerald-700/30">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-emerald-300">
                    {slot.start} - {slot.end}
                  </span>
                  <span className="badge bg-emerald-500/20 text-emerald-400">{slot.duration_minutes} min</span>
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
