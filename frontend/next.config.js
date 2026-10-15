/**
 * Phase D6 - security headers for everything the Next.js server serves
 * (app routes, assets, SSR payloads).
 *
 * Deliberate constraints, matched to backend/app/main.py::SECURITY_HEADERS so
 * the two layers never disagree:
 *
 * - NO Strict-Transport-Security. The deployment is HTTP-only today. HSTS on
 *   an HTTP origin is ignored by browsers, and enabling it before HTTPS is
 *   guaranteed can lock clients out of a server that never speaks TLS. It
 *   gets added together with the certificate, never before.
 * - The CSP is limited to frame-ancestors / base-uri / object-src /
 *   form-action. It deliberately does NOT declare default-src or script-src:
 *   Next.js and the app's inline styles/scripts would force unsafe-inline,
 *   so a "stricter-looking" policy would end up as a wildcard that is worse
 *   than an honest, narrow one. Tighten it when the asset pipeline allows.
 * - Permissions-Policy must keep microphone=(self): the app has an in-browser
 *   voice assistant, and a blanket `microphone=()` would silently break it.
 */
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Permissions-Policy",
    value:
      "camera=(self), microphone=(self), geolocation=(), payment=(), usb=(), serial=(), interest-cohort=()",
  },
  {
    key: "Content-Security-Policy",
    value:
      "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'",
  },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Keep `next dev` isolated from `next build`; both otherwise write to
  // `.next`, which can leave a running dev server referencing removed chunks.
  distDir: process.env.NEXT_DIST_DIR || (process.env.NODE_ENV === "development" ? ".next-dev" : ".next"),
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: SECURITY_HEADERS,
      },
    ];
  },
};

module.exports = nextConfig;
