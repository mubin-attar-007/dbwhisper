// Canonical public origin for the deployed site. Used by metadataBase, robots, and
// sitemap so they always agree. Overridable via NEXT_PUBLIC_SITE_URL (e.g. preview
// deployments); defaults to the production Vercel domain.
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://dbwhisper.vercel.app"
).replace(/\/+$/, "");
