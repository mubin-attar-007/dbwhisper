/** Shared external links for DBWhisper — used by the app sidebar and the marketing footer. */
export const LINKS = {
  github: "https://github.com/mubin-attar-007/dbwhisper",
  linkedin: "https://www.linkedin.com/in/mubin-attar-53223716a",
  huggingface: "https://huggingface.co/heisenbergblue",
  // FastAPI's Swagger UI is served on the backend's own origin (which sets no CSP),
  // so it renders correctly there. The same-origin `/api/docs` proxy came up blank
  // because the app's CSP (`script-src 'self'`) blocks Swagger's CDN bundle and the
  // page's hard-coded `/openapi.json` fetch isn't under the `/api` proxy prefix.
  apiDocs: "https://heisenbergblue-dbwhisper.hf.space/docs",
} as const;
