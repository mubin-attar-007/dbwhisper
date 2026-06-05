/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Same-origin proxy: browser calls /api/*, Next rewrites to the backend. This avoids
  // CORS entirely and hides the backend URL. Set API_PROXY_TARGET to the deployed API
  // origin (e.g. the Hugging Face Space URL) in Vercel; defaults to the local backend.
  async rewrites() {
    const target = (process.env.API_PROXY_TARGET ?? "http://localhost:8000").replace(/\/+$/, "");
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};

export default nextConfig;
