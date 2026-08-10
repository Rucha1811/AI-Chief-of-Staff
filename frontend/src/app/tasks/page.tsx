"use client";

import { useEffect, useState } from "react";
import { api, Task, TaskStats } from "@/lib/api";
import { CheckSquare, Plus, Sparkles, Trash2, RefreshCw, Clock, AlertCircle } from "lucide-react";
import BackButton from "@/components/BackButton";

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newTask, setNewTask] = useState({ title: "", description: "", priority: "medium", due_date: "" });
  const [reprioritizing, setReprioritizing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [t, s] = await Promise.all([api.tasks.list(), api.tasks.stats()]);
      setTasks(t);
      setStats(s);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const createTask = async () => {
    if (!newTask.title) return;
    await api.tasks.create({
      title: newTask.title,
      description: newTask.description || undefined,
      priority: newTask.priority,
      due_date: newTask.due_date ? new Date(newTask.due_date).toISOString() : undefined,
    });
    setNewTask({ title: "", description: "", priority: "medium", due_date: "" });
    setShowCreate(false);
    await load();
  };

  const updateStatus = async (id: number, status: string) => {
    await api.tasks.update(id, { status });
    await load();
  };

  const deleteTask = async (id: number) => {
    await api.tasks.delete(id);
    await load();
  };

  const reprioritize = async () => {
    setReprioritizing(true);
    await api.tasks.reprioritize();
    await load();
    setReprioritizing(false);
  };

  const priorityColors: Record<string, string> = {
    critical: "badge-critical",
    high: "badge-high",
    medium: "badge-medium",
    low: "badge-low",
  };

  const isOverdue = (task: Task) =>
    task.due_date && task.status !== "completed" && new Date(task.due_date) < new Date();

  return (
    <div className="space-y-6">
      {error && (
        <div className="card border-amber-200 bg-amber-50 flex items-center gap-3">
          <AlertCircle className="w-4 h-4 text-amber-600 shrink-0" />
          <p className="text-sm text-amber-700 flex-1">{error}</p>
          <button onClick={() => window.location.reload()} className="btn-secondary text-xs">Retry</button>
        </div>
      )}
      <div className="flex items-center justify-between">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3">Task Management</h1>
          <p className="text-slate-500 mt-1">
            {stats?.pending ?? 0} pending · {stats?.in_progress ?? 0} in progress · {stats?.overdue ?? 0} overdue
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={reprioritize} disabled={reprioritizing} className="btn-secondary flex items-center gap-1.5">
            <Sparkles className={`w-4 h-4 ${reprioritizing ? "animate-pulse" : ""}`} />
            AI Reprioritize
          </button>
          <button onClick={() => setShowCreate(!showCreate)} className="btn-primary flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Add Task
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="card border-pink-200 space-y-3">
          <h3 className="text-sm font-semibold text-slate-800">New Task</h3>
          <input
            placeholder="Task title"
            value={newTask.title}
            onChange={(e) => setNewTask({ ...newTask, title: e.target.value })}
            className="input"
          />
          <input
            placeholder="Description (optional)"
            value={newTask.description}
            onChange={(e) => setNewTask({ ...newTask, description: e.target.value })}
            className="input"
          />
          <div className="grid grid-cols-2 gap-2">
            <select value={newTask.priority} onChange={(e) => setNewTask({ ...newTask, priority: e.target.value })} className="input">
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <input type="date" value={newTask.due_date} onChange={(e) => setNewTask({ ...newTask, due_date: e.target.value })} className="input" />
          </div>
          <button onClick={createTask} className="btn-primary w-full">Create Task</button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            <div key={task.id} className={`card flex items-start gap-4 ${isOverdue(task) ? "border-red-200" : ""}`}>
              <button
                onClick={() => updateStatus(task.id, task.status === "completed" ? "pending" : "completed")}
                className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                  task.status === "completed"
                    ? "bg-emerald-500 border-emerald-500 text-white"
                    : "border-slate-300 hover:border-pink-400"
                }`}
              >
                {task.status === "completed" && <CheckSquare className="w-3 h-3" />}
              </button>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`text-sm font-medium ${task.status === "completed" ? "text-slate-500 line-through" : "text-slate-800"}`}>
                    {task.title}
                  </span>
                  <span className={priorityColors[task.priority] || "badge"}>{task.priority}</span>
                  {isOverdue(task) && <AlertCircle className="w-3.5 h-3.5 text-red-600" />}
                </div>
                {task.description && (
                  <p className="text-xs text-slate-500 mt-0.5">{task.description}</p>
                )}
                <div className="flex items-center gap-3 mt-1.5">
                  {task.due_date && (
                    <span className={`text-xs flex items-center gap-1 ${isOverdue(task) ? "text-red-600" : "text-slate-500"}`}>
                      <Clock className="w-3 h-3" />
                      {new Date(task.due_date).toLocaleDateString()}
                    </span>
                  )}
                  {task.estimated_minutes && (
                    <span className="text-xs text-slate-500">{task.estimated_minutes} min</span>
                  )}
                  <span className="text-xs text-slate-500 capitalize">via {task.source}</span>
                </div>
              </div>

              <div className="flex items-center gap-1">
                {task.status !== "completed" && (
                  <div className="flex gap-1">
                    {["critical", "high", "medium", "low"].map((p) => (
                      <button
                        key={p}
                        onClick={() => api.tasks.update(task.id, { priority: p }).then(load)}
                        className={`w-2 h-2 rounded-full ${
                          task.priority === p ? "ring-1 ring-offset-1 ring-offset-slate-800" : ""
                        } ${
                          p === "critical" ? "bg-red-500" : p === "high" ? "bg-orange-500" : p === "medium" ? "bg-yellow-500" : "bg-slate-500"
                        }`}
                      />
                    ))}
                  </div>
                )}
                <button onClick={() => deleteTask(task.id)} className="btn-ghost p-1 ml-2 text-slate-500 hover:text-red-600">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}

          {tasks.length === 0 && (
            <div className="card text-center py-12 text-slate-500">
              <CheckSquare className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No tasks yet. Create one to get started.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
