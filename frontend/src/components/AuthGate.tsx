"use client";

import { useEffect, useState } from "react";
import { api, setToken } from "@/lib/api";
import { getGoogleLoginUrl } from "@/lib/config";
import { Lock, LogIn, KeyRound, ChevronDown, ChevronUp } from "lucide-react";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"loading" | "auth" | "app">("loading");
  const [password, setPassword] = useState("");
  const [emergencyEmail, setEmergencyEmail] = useState("");
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
    const params = new URLSearchParams(window.location.search);

    // Handle errors redirected back from the Google OAuth callback
    const oauthError = params.get("error");
    if (oauthError) {
      window.history.replaceState({}, document.title, window.location.pathname);
      const reason = params.get("reason");
      const detail = params.get("detail");
      if (oauthError === "oauth_failed") {
        setError(`Google sign-in failed: ${detail || "unknown error"}. Please try again.`);
      } else if (oauthError === "invalid_state") {
        setError("Sign-in session expired. Please try again.");
      } else if (oauthError === "no_email") {
        setError("Google did not share your email address. Check your Google account permissions.");
      } else if (oauthError === "account_inactive") {
        setError("Your account is inactive. Contact an administrator.");
      } else if (oauthError === "access_denied" && reason === "not_invited") {
        setError("This Google account is not approved for the app yet. Add it under Google Cloud Console → Google Auth Platform → Audience → Test users, or use the password account provided by the app owner.");
      } else if (oauthError === "access_denied") {
        setError("Google blocked this sign-in. The app owner must approve this account in Google Cloud, or publish and verify the OAuth app.");
      } else {
        setError(`Sign-in error: ${detail || reason || oauthError}. Please try again.`);
      }
      setState("auth");
      return;
    }

    // Google sign-in redirects back with a single-use exchange code
    // (never the session token itself). Trade it for the token, then scrub
    // the URL so neither code nor token lingers in browser history.
    const code = params.get("code");
    if (code) {
      window.history.replaceState({}, document.title, window.location.pathname);
      api.auth
        .exchange(code)
        .then((res) => {
          setToken(res.token);
        // Google sign-in establishes the app session first. Immediately
        // continue into the per-user Google integration consent flow so a
        // new local account does not need a second manual setup step.
        return api.integrations.googleAuthorize();
      })
      .then((integration) => {
        window.location.href = integration.url;
      })
      .catch(() => {
        setError("Google sign-in or integration setup failed. Please try again.");
        setState("auth");
      });
      return;
    }

    check();
    window.addEventListener("aichief:auth", check);
    return () => window.removeEventListener("aichief:auth", check);
  }, []);

  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = showCode
        ? await api.auth.emergencyLogin(emergencyEmail.trim(), emergencyCode.trim())
        : await api.auth.login(password);
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

          <button
            type="button"
            onClick={() => {
              window.location.href = getGoogleLoginUrl();
            }}
            className="w-full flex items-center justify-center gap-3 px-4 py-2.5 bg-white hover:bg-gray-50 text-gray-900 text-sm font-medium rounded-xl transition-all shadow-sm border border-gray-200 mb-6"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            Sign in with Google
          </button>

          <div className="relative flex items-center mb-6">
            <div className="flex-grow border-t border-slate-700/30"></div>
            <span className="flex-shrink-0 mx-4 text-slate-500 text-xs">OR USE PASSWORD</span>
            <div className="flex-grow border-t border-slate-700/30"></div>
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
              <>
                <input
                  type="email"
                  className="input"
                  placeholder="Account email"
                  value={emergencyEmail}
                  onChange={(e) => setEmergencyEmail(e.target.value)}
                  autoFocus
                />
                <input
                  type="text"
                  className="input"
                  placeholder="Emergency code"
                  value={emergencyCode}
                  onChange={(e) => setEmergencyCode(e.target.value)}
                />
              </>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <p className="text-xs text-slate-500">
              Google sign-in is limited by Google&apos;s OAuth consent-screen status. For a new Google account, the app owner must add it as a test user or complete Google verification.
            </p>
            <button
              type="submit"
              disabled={busy || (showCode ? !emergencyEmail || !emergencyCode : !password)}
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
