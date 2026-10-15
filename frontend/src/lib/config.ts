/**
 * Central API configuration - the ONE place in the frontend that may name a
 * backend origin.
 *
 * Why a single module
 * -------------------
 * Every other file resolves the API base URL through `getApiBaseUrl()`. That
 * makes the build-time/runtime contract auditable: the only hardcoded backend
 * origin in `frontend/src` is the development fallback declared below, and
 * `backend/tests/test_phase_d_frontend_api.py` fails if a second one appears
 * anywhere else (including the exact `AuthGate.tsx` bug that used to hardcode
 * `http://localhost:8000/api/auth/google/login`).
 *
 * Build-time, not runtime
 * -----------------------
 * `NEXT_PUBLIC_*` variables are inlined into the browser bundle by
 * `next build`. A runtime `environment:` entry in docker-compose therefore
 * CANNOT change an already-built bundle - it only looks like it can. The
 * production image takes the value as a build argument (frontend/Dockerfile),
 * and docker-compose.prod.yml passes `${FRONTEND_API_URL:?...}` into
 * `build.args`, so a production image cannot be built without it.
 *
 * No secret ever belongs in a NEXT_PUBLIC_* variable: everything here ships
 * to the browser.
 */

/**
 * Development fallback. Used only when no build-time value was supplied AND
 * the page is served from (or rendered for) a local development host.
 * Production must set NEXT_PUBLIC_API_URL at build time.
 */
const DEV_FALLBACK_API_URL = "http://localhost:8000";

/**
 * Accept only an absolute http(s) URL. Anything else (a typo, a bare host, a
 * `javascript:` value) is discarded rather than concatenated into a fetch
 * target, and trailing slashes are trimmed so `${base}/api/...` never
 * produces a double slash.
 */
function normalizeBaseUrl(raw: string | undefined | null): string | null {
  if (!raw) return null;
  const value = raw.trim();
  if (!value) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null; // credentials in a base URL are never intentional
  return value.replace(/\/+$/, "");
}

/**
 * The API base URL for this session.
 *
 * Order:
 *   1. `NEXT_PUBLIC_API_URL`, inlined at build time (production path).
 *   2. Development fallback when served from a local dev host.
 *   3. Same origin (https) - the documented reverse-proxy layout, where the
 *      proxy serves the app and forwards `/api` to the backend.
 *   4. Current host on port 8000 (plain-http dev server reached by IP).
 *
 * Case 3/4 are derived from `window.location`, never from a hardcoded origin.
 */
export function getApiBaseUrl(): string {
  const configured = normalizeBaseUrl(process.env.NEXT_PUBLIC_API_URL);
  if (configured) return configured;

  if (typeof window === "undefined") return DEV_FALLBACK_API_URL;

  const { protocol, hostname } = window.location;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return DEV_FALLBACK_API_URL;
  }
  if (protocol === "https:") return `${protocol}//${hostname}`;
  return `${protocol}//${hostname}:8000`;
}

/** Relative path of the anonymous Google sign-in entry point. */
export const GOOGLE_LOGIN_PATH = "/api/auth/google/login";

/** Absolute URL for the anonymous Google sign-in entry point. */
export function getGoogleLoginUrl(): string {
  return `${getApiBaseUrl()}${GOOGLE_LOGIN_PATH}`;
}
