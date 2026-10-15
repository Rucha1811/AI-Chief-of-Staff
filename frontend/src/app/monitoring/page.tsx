"use client";

import { useEffect, useState } from "react";
import { api, PublicHealth, SystemInfo } from "@/lib/api";
import { Activity, Database, Cpu, Gauge, RefreshCw, AlertTriangle, CheckCircle2, XCircle, Inbox, Zap, Timer } from "lucide-react";
import BackButton from "@/components/BackButton";

export default function MonitoringPage() {
  // `health` is the PUBLIC probe (Phase D7: status only, no detail). `info`
  // is the authenticated /api/system/info and is where every operational
  // number on this page now comes from. The two are fetched separately on
  // purpose: a degraded backend answers /api/health with 503, and that must
  // show as a degraded card rather than blanking out the whole page.
  const [health, setHealth] = useState<PublicHealth | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = async () => {
    try {
      const [h, i] = await Promise.all([
        api.monitoring
          .health()
          .catch(() => ({ status: "error" } as PublicHealth)),
        api.monitoring.systemInfo(),
      ]);
      setHealth(h);
      setInfo(i);
      setError(null);
      setLastUpdated(new Date());
    } catch (err: any) {
      setError(err.message || "Failed to load monitoring data");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
    const interval = setInterval(() => {
      if (autoRefresh) load();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh]);

  const queue = info?.ai.ai_queue;
  // Every operational number below comes from the authenticated
  // /api/system/info payload. The public /api/health probe contributes only
  // its one-bit `status`, which is all it is allowed to reveal.
  const sys = info?.system;
  const db = sys?.database;
  const ollama = info?.ai.ollama;
  const pool = db?.pool;
  const poolUsed = pool && pool.checked_out !== null
    ? pool.checked_out / Math.max(1, pool.size + pool.overflow)
    : null;
  const failureRate = queue && queue.processed + queue.failed > 0
    ? (queue.failed / (queue.processed + queue.failed)) * 100
    : null;
  const failureLoading = queue && queue.processed > 0;
  const cb = info?.ai.circuit_breaker || ollama?.circuit_breaker;

  const statusBadge = (ok: boolean) =>
    ok ? (
      <span className="badge-high flex items-center gap-1">
        <CheckCircle2 className="w-3.5 h-3.5" /> OK
      </span>
    ) : (
      <span className="badge-critical flex items-center gap-1">
        <XCircle className="w-3.5 h-3.5" /> DEGRADED
      </span>
    );

  const cbColor = (s?: string) =>
    s === "CLOSED" ? "text-emerald-600 bg-emerald-50" : s === "HALF_OPEN" ? "text-amber-600 bg-amber-50" : "text-red-600 bg-red-50";

  const card = (title: string, ok: boolean, children: React.ReactNode, icon: React.ReactNode) => (
    <div className={`card ${ok ? "border-slate-200" : "border-red-300 bg-red-50/50"}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          {icon}
          <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
        </div>
        {statusBadge(ok)}
      </div>
      {children}
    </div>
  );

  const row = (label: string, value: React.ReactNode, extra?: React.ReactNode) => (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <div className="flex items-center gap-2">
        {extra}
        <span className="text-sm font-medium text-slate-800">{value}</span>
      </div>
    </div>
  );

  const progressBar = (pct: number | null, color: string) => {
    if (pct === null) return <div className="text-xs text-slate-400">n/a</div>;
    return (
      <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden mt-2">
        <div
          className={`h-full ${color} transition-all duration-700`}
          style={{ width: `${Math.min(100, pct * 100)}%` }}
        />
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3">System Monitoring</h1>
          <p className="text-slate-500 mt-1">
            Reliability, queue performance and AI health
            {lastUpdated && ` · updated ${lastUpdated.toLocaleTimeString()}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-slate-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="accent-pink-500"
            />
            Auto-refresh (10s)
          </label>
          <button onClick={() => { setRefreshing(true); load(); }} disabled={refreshing} className="btn-secondary flex items-center gap-2">
            <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="card border-amber-200 bg-amber-50 flex items-center gap-3">
          <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
          <p className="text-sm text-amber-700 flex-1">{error}</p>
          <button onClick={load} className="btn-secondary text-xs">Retry</button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="card text-center py-4">
              <div className="stat-value text-lg text-emerald-600">{health?.status === "ok" ? "HEALTHY" : "ERROR"}</div>
              <div className="text-xs text-slate-500 flex items-center justify-center gap-1.5 mt-1">
                <Activity className="w-3.5 h-3.5" /> {sys?.app} v{sys?.version}
              </div>
            </div>
            <div className="card text-center py-4">
              <div className="stat-value text-lg text-slate-900">{queue?.processed ?? 0}</div>
              <div className="text-xs text-slate-500">jobs processed</div>
            </div>
            <div className="card text-center py-4">
              <div className="stat-value text-lg text-slate-900">{queue?.queue_size ?? 0}</div>
              <div className="text-xs text-slate-500">in AI queue</div>
            </div>
            <div className="card text-center py-4">
              <div className={`stat-value text-lg ${failureRate === null || failureRate >= 20 ? "text-red-600" : "text-slate-900"}`}>
                {failureRate === null ? "—" : `${failureRate.toFixed(1)}%`}
              </div>
              <div className="text-xs text-slate-500">failure rate</div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {card(
              "Database",
              db?.status === "ok",
              <>
                {row("Status", db?.status === "ok" ? "Connected" : db?.detail || "error")}
                {row(
                  "Connections in use",
                  pool === undefined || pool.checked_out === null ? "n/a" : `${pool.checked_out} / ${pool.size + pool.overflow}`,
                  progressBar(poolUsed, poolUsed !== null && poolUsed > 0.8 ? "bg-red-500" : "bg-emerald-500")
                )}
                {row("Pool size / overflow", `${pool?.size ?? "?"} / +${pool?.overflow ?? "?"}`)}
                {row("Overflow currently used", pool?.overflow_used ?? 0)}
              </>,
              <Database key="db-icon" className="w-4 h-4 text-pink-500" />
            )}

            {card(
              "Ollama (AI)",
              ollama?.status === "ok",
              <>
                {row("Model", ollama?.model || "?")}
                {row(
                  "Status",
                  ollama?.status === "ok"
                    ? "Model available"
                    : ollama?.status ? ollama.status.replace("_", " ") : "unavailable"
                )}
                {row("Circuit breaker", cb, (
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cbColor(cb)}`}>{cb}</span>
                ))}
                {row("Connection", ollama?.available ? "reachable" : "unreachable")}
              </>,
              <Cpu key="cpu-icon" className="w-4 h-4 text-pink-500" />
            )}

            {card(
              "AI Worker Queue",
              queue !== undefined && failureRate !== null && failureRate < 20,
              <>
                {row("Active workers", queue?.active ?? 0)}
                {row("Queued jobs", queue?.queue_size ?? 0)}
                {row("Processed", queue?.processed ?? 0)}
                {row("Failed", queue?.failed ?? 0)}
                {row("Uptime", queue?.uptime_seconds ? `${Math.floor(queue.uptime_seconds / 60)}m` : "n/a")}
                {failureLoading && (
                  <div className="mt-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-slate-500">Success rate</span>
                      <span className="font-medium text-slate-700">{failureRate!.toFixed(1)}% failures</span>
                    </div>
                    <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden mt-1">
                      <div
                        className={`h-full ${failureRate! >= 20 ? "bg-red-500" : failureRate! > 5 ? "bg-amber-500" : "bg-emerald-500"} transition-all duration-700`}
                        style={{ width: `${Math.min(100, 100 - failureRate!)}%` }}
                      />
                    </div>
                  </div>
                )}
              </>,
              <Inbox key="queue-icon" className="w-4 h-4 text-pink-500" />
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="card">
              <div className="flex items-center gap-2 mb-3">
                <Gauge className="w-4 h-4 text-pink-500" />
                <h3 className="text-sm font-semibold text-slate-700">n8n Integration</h3>
              </div>
              {row("Ingest endpoint", sys?.integrations.n8n_ingest_enabled ? "Enabled" : "Disabled")}
              {row("Batch size limit", sys?.integrations.ingest_batch_max ?? "n/a")}
            </div>

            <div className="card">
              <div className="flex items-center gap-2 mb-3">
                <Timer className="w-4 h-4 text-pink-500" />
                <h3 className="text-sm font-semibold text-slate-700">System</h3>
              </div>
              {row("App", sys?.app ?? "n/a")}
              {row("Version", sys?.version ?? "n/a")}
              {row("Overall status", health?.status ?? "n/a")}
              <div className="mt-2 flex items-center gap-2 text-xs text-slate-400">
                <Zap className="w-3.5 h-3.5" />
                Polls /api/health (public, status only) and /api/system/info
                (authenticated, full detail) every 10 seconds.
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}