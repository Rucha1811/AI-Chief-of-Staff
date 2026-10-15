"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, GoogleAuthHealth, setToken } from "@/lib/api";
import {
  Settings,
  Plug,
  Unplug,
  Mail,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ExternalLink,
  User,
  Shield,
  LogOut,
} from "lucide-react";
import BackButton from "@/components/BackButton";

export default function SettingsPage() {
  const searchParams = useSearchParams();
  const [health, setHealth] = useState<GoogleAuthHealth | null>(null);
  const [connections, setConnections] = useState<
    Array<{ provider: string; account_id: string | null; status: string; last_synced_at: string | null; last_error: string | null }>
  >([]);
  const [me, setMe] = useState<{ email: string; display_name: string | null; roles: string[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error" | "info"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [h, conns, profile] = await Promise.all([
        api.integrations.googleHealth(),
        api.integrations.user.list(),
        api.auth.me(),
      ]);
      setHealth(h);
      setConnections(conns);
      setMe(profile);
    } catch (err: any) {
      setNotice({ kind: "error", text: err.message || "Failed to load settings." });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const google = searchParams.get("google");
    if (google === "success") setNotice({ kind: "success", text: "Google connected successfully. Your Gmail, Calendar and Classroom will start syncing." });
    else if (google === "error") setNotice({ kind: "error", text: `Google authorization failed: ${searchParams.get("detail") || searchParams.get("reason") || "unknown error"}` });
    load();
    window.history.replaceState({}, document.title, window.location.pathname);
  }, [searchParams, load]);

  const connectGoogle = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.integrations.googleAuthorize();
      window.location.href = res.url;
    } catch (err: any) {
      setNotice({ kind: "error", text: err.message || "Could not start Google authorization." });
      setBusy(false);
    }
  };

  const disconnectGoogle = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.integrations.googleDisconnect();
      setNotice({ kind: "info", text: res.revoked ? "Google connection disconnected." : "No Google connection was active." });
      await load();
    } catch (err: any) {
      setNotice({ kind: "error", text: err.message || "Failed to disconnect Google." });
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      // Revoke every token for this account on the server, then drop the local
      // copy. Even if the network call fails we still clear the local token so
      // the UI never keeps a session the user asked to end.
      await api.auth.logout();
    } catch {
      /* best effort */
    } finally {
      setToken(null);
      setBusy(false);
    }
  };

  const connected = health?.connected ?? false;
  const googleRow = connections.find((c) => c.provider === "google");
  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <BackButton />
        <h1 className="text-2xl font-bold text-gradient mt-3 flex items-center gap-2">
          <Settings className="w-6 h-6 text-pink-500" /> Settings
        </h1>
        <p className="text-slate-500 mt-1">
          Connected accounts and integrations. Each user syncs their own Gmail, Calendar and Classroom.
        </p>
      </div>

      {notice && (
        <div
          className={`card flex items-center gap-3 ${
            notice.kind === "success"
              ? "border-emerald-200 bg-emerald-50"
              : notice.kind === "error"
                ? "border-red-200 bg-red-50"
                : "border-slate-200 bg-slate-50"
          }`}
        >
          {notice.kind === "success" ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
          ) : notice.kind === "error" ? (
            <XCircle className="w-4 h-4 text-red-600 shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 text-slate-600 shrink-0" />
          )}
          <p className={`text-sm flex-1 ${notice.kind === "success" ? "text-emerald-700" : notice.kind === "error" ? "text-red-700" : "text-slate-700"}`}>
            {notice.text}
          </p>
          <button onClick={() => setNotice(null)} className="text-xs text-slate-400 hover:text-slate-600">
            Dismiss
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : (
        <>
          {/* Account */}
          <div className="card">
            <div className="flex items-center gap-2 mb-3">
              <User className="w-4 h-4 text-pink-500" />
              <h3 className="text-sm font-semibold text-slate-700">Your account</h3>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-xs text-slate-500">Email</span>
                <span className="font-medium text-slate-800">{me?.email ?? "—"}</span>
              </div>
              <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-xs text-slate-500">Name</span>
                <span className="font-medium text-slate-800">{me?.display_name ?? "—"}</span>
              </div>
              <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-xs text-slate-500">Role</span>
                <span className="font-medium text-slate-800 flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-pink-500" />
                  {(me?.roles ?? []).join(", ") || "member"}
                </span>
              </div>
            </div>
            <button
              onClick={signOut}
              disabled={busy}
              className="btn-secondary mt-3 flex items-center gap-2"
            >
              <LogOut className="w-4 h-4" /> {busy ? "Signing out…" : "Sign out"}
            </button>
          </div>

          {/* Google */}
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Mail className="w-4 h-4 text-pink-500" />
                <h3 className="text-sm font-semibold text-slate-700">Google (Gmail · Calendar · Classroom)</h3>
              </div>
              {connected ? (
                <span className="badge-high flex items-center gap-1">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Connected
                </span>
              ) : (
                <span className="badge-critical flex items-center gap-1">
                  <XCircle className="w-3.5 h-3.5" /> Not connected
                </span>
              )}
            </div>

            <div className="space-y-2 text-sm mb-4">
              <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-xs text-slate-500">Connected account</span>
                <span className="font-medium text-slate-800">{health?.email ?? googleRow?.account_id ?? "—"}</span>
              </div>
              <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-xs text-slate-500">Status</span>
                <span className="font-medium text-slate-800 capitalize">
                  {connected ? "Healthy" : (health?.reason ?? "not connected")?.replace("_", " ")}
                </span>
              </div>
              {health?.expires_at && (
                <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
                  <span className="text-xs text-slate-500">Token expiry</span>
                  <span className="font-medium text-slate-800">{new Date(health.expires_at).toLocaleString()}</span>
                </div>
              )}
            </div>

            {connected ? (
              <button onClick={disconnectGoogle} disabled={busy} className="btn-secondary flex items-center gap-2">
                <Unplug className="w-4 h-4" /> {busy ? "Working…" : "Disconnect Google"}
              </button>
            ) : (
              <div className="space-y-3">
                <button onClick={connectGoogle} disabled={busy} className="btn-primary flex items-center gap-2">
                  <Plug className="w-4 h-4" /> {busy ? "Opening Google…" : "Connect Google"}
                </button>
                <p className="text-xs text-slate-400 flex items-start gap-1.5">
                  <ExternalLink className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  You'll be redirected to Google to grant Gmail (read), Calendar (events) and Classroom (read) access. Tokens are stored encrypted and are only used for your own sync.
                </p>
              </div>
            )}
          </div>

          {/* Troubleshooting */}
          <div className="card border-slate-200">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle className="w-4 h-4 text-amber-500" />
              <h3 className="text-sm font-semibold text-slate-700">Having trouble connecting?</h3>
            </div>
            <ul className="text-xs text-slate-500 space-y-1.5 list-disc list-inside">
              <li>If "Google OAuth is not configured" appears, the instance admin must add the Google OAuth client credentials to the backend environment and restart.</li>
              <li>Refresh the status to see the latest connection state.</li>
              <li>Unverified testing OAuth apps expire after 7 days — just disconnect and reconnect.</li>
            </ul>
          </div>
</>
    )}
    </div>
  );
}