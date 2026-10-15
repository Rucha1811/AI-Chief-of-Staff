"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type EGovStatus, type EGovState } from "@/lib/api";
import {
  GraduationCap,
  RefreshCw,
  Upload,
  Link2,
  Unplug,
  AlertTriangle,
  Clock,
  ShieldCheck,
} from "lucide-react";

const STATE_META: Record<
  EGovState,
  { label: string; badge: string; hint: string }
> = {
  not_eligible: {
    label: "Not available",
    badge: "bg-slate-100 text-slate-600",
    hint: "This account is not a verified CHARUSAT institutional account.",
  },
  connected: {
    label: "Connected",
    badge: "bg-emerald-100 text-emerald-700",
    hint: "Attendance was read from e-Governance.",
  },
  stale: {
    label: "Stale",
    badge: "bg-amber-100 text-amber-700",
    hint: "Showing the last successful sync. Refresh to update it.",
  },
  reauth_required: {
    label: "Sign-in required",
    badge: "bg-orange-100 text-orange-700",
    hint: "The portal session expired. Sign in again and reconnect.",
  },
  unavailable: {
    label: "Unavailable",
    badge: "bg-red-100 text-red-700",
    hint: "The portal could not be reached. Any rows below are the last known good data.",
  },
  not_connected: {
    label: "Not connected",
    badge: "bg-slate-100 text-slate-600",
    hint: "No e-Governance attendance is connected for your account yet.",
  },
};

function fmt(ts: string | null): string {
  if (!ts) return "never";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

export default function EGovAttendanceCard() {
  const [status, setStatus] = useState<EGovStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [mode, setMode] = useState<"none" | "session" | "import">("none");
  const [cookie, setCookie] = useState("");
  const [url, setUrl] = useState("");
  const [accountId, setAccountId] = useState("");
  const [html, setHtml] = useState("");
  const [term, setTerm] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await api.egov.get());
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load e-Governance attendance");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (fn: () => Promise<EGovStatus>, okMsg: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await fn();
      setStatus(next);
      setNotice(okMsg);
      return true;
    } catch (err: any) {
      setError(err.message || "Request failed");
      // Refresh the shown state so a failure's effect (expired/unavailable)
      // is reflected instead of a stale optimistic view.
      try {
        setStatus(await api.egov.get());
      } catch {
        /* keep the previous status */
      }
      return false;
    } finally {
      setBusy(false);
    }
  };

  const connectSession = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = await act(async () =>
      api.egov.connectSession({
        session_cookie: cookie,
        attendance_url: url,
        account_id: accountId || undefined,
      }),
    );
    if (ok) {
      // Never keep the cookie in component state or the DOM.
      setCookie("");
      setMode("none");
    }
  };

  const importHtml = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = await act(async () =>
      api.egov.importHtml({ html, term: term || undefined }),
    );
    if (ok) {
      setHtml("");
      setMode("none");
    }
  };

  const act = useCallback(
    async (fn: () => Promise<EGovStatus>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const next = await fn();
        setStatus(next);
        setNotice("Attendance updated.");
        return true;
      } catch (err: any) {
        setError(err?.message || "Request failed");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const meta = STATE_META[status?.state ?? "not_connected"];

  return (
    <div className="card space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <GraduationCap className="w-6 h-6 text-pink-400 mt-0.5" />
          <div>
            <h2 className="text-sm font-semibold text-slate-800">
              CHARUSAT e-Governance attendance
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Read-only. Signs in as you (solving its CAPTCHA); never edits
              attendance.
            </p>
          </div>
        </div>
        <span className={`badge ${meta.badge}`}>{meta.label}</span>
      </div>

      {status && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" />
            Last sync: {fmt(status.last_synced_at)}
            {status.stale && <span className="text-amber-600 font-medium">· stale</span>}
          </span>
          {status.method && <span>Method: {status.method === "import" ? "page import" : "session"}</span>}
          {status.account && <span>Account: {status.account}</span>}
          {status.record_count > 0 && (
            <span>{status.record_count} subject{status.record_count === 1 ? "" : "s"}</span>
          )}
        </div>
      )}

      <p className="text-xs text-slate-500">{meta.hint}</p>

      {(error || status?.last_error) && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
          <p className="text-xs text-amber-700">{error || status?.last_error}</p>
        </div>
      )}
      {notice && !error && (
        <p className="text-xs text-emerald-600 flex items-center gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5" /> {notice}
        </p>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {status?.connected && status.method === "session" && (
          <button
            className="btn-secondary text-xs flex items-center gap-1.5"
            disabled={busy}
            onClick={() => act(() => api.egov.refresh())}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} /> Refresh now
          </button>
        )}
        <button
          onClick={() => setMode((m) => (m === "session" ? "none" : "session"))}
          className="btn-secondary text-xs flex items-center gap-1.5"
        >
          <Link2 className="w-3.5 h-3.5" /> {status?.connected && status?.method === "session" ? "Replace session" : "Connect session"}
        </button>
        <button
          onClick={() => setMode((m) => (m === "import" ? "none" : "import"))}
          className="btn-secondary text-xs flex items-center gap-1.5"
        >
          <Upload className="w-3.5 h-3.5" /> Import saved page
        </button>
        {status?.connected && (
          <button
            onClick={() => act(async () => {
              await api.egov.disconnect();
              return api.egov.get();
            })}
            disabled={busy}
            className="btn-secondary text-xs flex items-center gap-1.5 text-red-600"
          >
            <Unplug className="w-3.5 h-3.5" /> Disconnect
          </button>
        )}
      </div>

      {/* Connect: session cookie */}
      {mode === "session" && (
        <form onSubmit={connectSession} className="space-y-3 border-t border-slate-100 pt-4">
          <p className="text-xs text-slate-500">
            Sign in to e-Governance in your browser, open your attendance page,
            then paste the page URL and your browser session cookie. The cookie
            is stored encrypted and never shown again.
          </p>
          <div className="grid md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500">Attendance page URL *</label>
              <input
                className="input mt-1"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://support.charusat.edu.in/Egov/....aspx"
                required
              />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Session cookie *</label>
              <input
                className="input mt-1"
                type="password"
                autoComplete="off"
                value={cookie}
                onChange={(e) => setCookie(e.target.value)}
                placeholder="ASP.NET_SessionId=…"
                required
              />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500">Enrolment / username (optional)</label>
              <input
                className="input mt-1"
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
                placeholder="e.g. 24DCS025"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Connecting…" : "Connect & sync"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode("none")}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Import: saved HTML */}
      {mode === "import" && (
        <form onSubmit={importHtml} className="space-y-3 border-t border-slate-100 pt-4">
          <p className="text-xs text-slate-500">
            No credentials needed: in your signed-in browser open the attendance
            page, save it (Ctrl/Cmd+S → “Webpage, HTML only”) or copy the page
            source, and paste it here.
          </p>
          <div className="grid md:grid-cols-3 gap-3">
            <div className="md:col-span-1">
              <label className="text-xs font-medium text-slate-500">Term / semester (optional)</label>
              <input className="input mt-1" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="e.g. Sem 5" />
            </div>
          </div>
          <textarea
            className="input min-h-[120px] font-mono text-xs"
            value={html}
            onChange={(e) => setHtml(e.target.value)}
            placeholder="<html> … paste the saved attendance page here … </html>"
            required
          />
          <div className="flex gap-3">
            <button type="submit" className="btn-primary flex items-center gap-2" disabled={busy}>
              <Upload className="w-4 h-4" /> {busy ? "Importing…" : "Import page"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode("none")}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Subject-level table */}
      {loading && !status ? (
        <div className="flex justify-center py-8">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-pink-400" />
        </div>
      ) : status && status.records.length > 0 ? (
        <div className="border-t border-slate-100 pt-3">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="font-medium py-1.5 pr-3">Subject</th>
                  <th className="font-medium py-1.5 px-3">Term</th>
                  <th className="font-medium py-1.5 px-3 text-right">Held</th>
                  <th className="font-medium py-1.5 px-3 text-right">Attended</th>
                  <th className="font-medium py-1.5 pl-3 text-right">Attendance</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {status.records.map((r) => (
                  <tr key={r.id}>
                    <td className="py-2 pr-3">
                      <span className="font-medium text-slate-800">{r.subject_name}</span>
                      {r.subject_code && (
                        <span className="text-xs text-slate-400 ml-1.5">{r.subject_code}</span>
                      )}
                    </td>
                    <td className="px-3 text-slate-500">{r.term || "—"}</td>
                    <td className="px-3 text-right text-slate-600">
                      {r.classes_held ?? "—"}
                    </td>
                    <td className="px-3 text-right text-slate-500">{r.classes_attended ?? "—"}</td>
                    <td className="pl-3 text-right">
                      {r.percentage === null ? (
                        // No verified numerator+denominator: show that it is
                        // unknown rather than inventing a number.
                        <span className="text-slate-300">—</span>
                      ) : (
                        <span
                          className={`badge ${
                            r.percentage >= 75
                              ? "bg-emerald-100 text-emerald-700"
                              : r.percentage >= 50
                                ? "bg-amber-100 text-amber-700"
                                : "bg-red-100 text-red-700"
                          }`}
                        >
                          {r.percentage}%
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {status.overall_percentage !== null && (
            <p className="text-xs text-slate-500 mt-3">
              Overall (from verified counts):{" "}
              <span className="font-semibold text-slate-700">
                {status.overall_percentage}%
              </span>{" "}
              ({status.holdings.attended}/{status.holdings.held})
            </p>
          )}
        </div>
      ) : status && status.state === "not_connected" ? (
        <div className="border-t border-slate-100 pt-4 text-center">
          <Link2 className="w-8 h-8 text-slate-300 mx-auto mb-2" />
          <p className="text-xs text-slate-500">
            Connect or import your e-Governance attendance to see it here.
          </p>
        </div>
      ) : status ? (
        <div className="border-t border-slate-100 pt-4 text-center">
          <RefreshCw className="w-8 h-8 text-slate-300 mx-auto mb-2" />
          <p className="text-xs text-slate-500">
            Connected, but the portal returned no attendance rows.
          </p>
        </div>
      ) : null}
    </div>
  );
}
