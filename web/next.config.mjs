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
  // Same-origin proxy: browser calls /api/*, Next rewrites to the backend. This avoids
  // CORS entirely and hides the backend URL. Set API_PROXY_TARGET to the deployed API
  // origin (e.g. the Hugging Face Space URL) in Vercel; defaults to the local backend.
  async rewrites() {
    const target = (process.env.API_PROXY_TARGET ?? "http://localhost:8000").replace(/\/+$/, "");
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
