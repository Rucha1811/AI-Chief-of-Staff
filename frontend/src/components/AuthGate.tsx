"use client";

import { useEffect, useState } from "react";
import { api, setToken } from "@/lib/api";
import { Lock, LogIn, KeyRound, ChevronDown, ChevronUp } from "lucide-react";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"loading" | "auth" | "app">("loading");
  const [password, setPassword] = useState("");
  const [emergencyCode, setEmergencyCode] = useState("");
  const [showCode, setShowCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    try {
      const s = await api.auth.status();
      setState(s.enabled && !s.authenticated ? "auth" : "app");
    } catch {
      setState("app");
    }
  };

  useEffect(() => {
    check();
    window.addEventListener("aichief:auth", check);
    return () => window.removeEventListener("aichief:auth", check);
  }, []);

  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.auth.login(password, showCode ? emergencyCode : undefined);
      setToken(res.token);
      setState("app");
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  if (state === "loading") {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
      </div>
    );
  }

  if (state === "auth") {
    return (
      <div className="flex items-center justify-center min-h-screen p-6">
        <div className="card w-full max-w-sm gradient-top-border">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 rounded-xl bg-instagram">
              <Lock className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gradient">Welcome back</h1>
              <p className="text-sm text-slate-500">This app is password protected</p>
            </div>
          </div>
          <form onSubmit={login} className="space-y-4">
            {!showCode ? (
              <input
                type="password"
                className="input"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
              />
            ) : (
              <input
                type="text"
                className="input"
                placeholder="Emergency code"
                value={emergencyCode}
                onChange={(e) => setEmergencyCode(e.target.value)}
                autoFocus
              />
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={busy || (showCode ? !emergencyCode : !password)}
              className="btn-primary w-full flex items-center justify-center gap-2"
            >
              {showCode ? <KeyRound className="w-4 h-4" /> : <LogIn className="w-4 h-4" />}
              {busy ? "Signing in…" : showCode ? "Use emergency code" : "Sign in"}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowCode((v) => !v);
                setError(null);
              }}
              className="w-full text-xs text-slate-500 hover:text-pink-600 flex items-center justify-center gap-1"
            >
              {showCode ? (
                <>Back to password <ChevronUp className="w-3 h-3" /></>
              ) : (
                <>Forgot your password? Use an emergency code <ChevronDown className="w-3 h-3" /></>
              )}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
