/** @type {import('next').NextConfig} */

// Baseline security headers. CSP is intentionally pragmatic for a Next app without nonces:
// 'unsafe-inline' is allowed for scripts/styles (Next runtime + Tailwind) but NOT 'unsafe-eval'.
// connect-src is 'self' because the browser only ever calls same-origin /api/* (proxied).
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "geolocation=(), microphone=(), camera=()" },
];

const nextConfig = {
  reactStrictMode: true,
  // Emit .next/standalone: server.js plus only the node_modules the traced entrypoints actually
  // reach. This is what makes web/Dockerfile's runtime stage small enough to be worth shipping --
  // the alternative is copying the whole 500 MB install tree and running `next start`. Vercel
  // ignores this setting, so turning it on costs that deployment nothing.
  output: "standalone",
  // Same-origin proxy: browser calls /api/*, Next rewrites to the backend. This avoids
  // CORS entirely and hides the backend URL. Set API_PROXY_TARGET to the deployed API
  // origin (e.g. the Hugging Face Space URL) in Vercel; defaults to the local backend.
  //
  // BUILD-TIME, not request-time. Next calls rewrites() during `next build` and freezes the
  // resolved destination into .next/routes-manifest.json; `next start` and the standalone server
  // read that file and never re-evaluate this function. Changing API_PROXY_TARGET therefore
  // requires a rebuild (Vercel does one per deploy, and web/Dockerfile takes it as a build ARG).
  async rewrites() {
    const target = (process.env.API_PROXY_TARGET ?? "http://localhost:8000").replace(/\/+$/, "");
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
