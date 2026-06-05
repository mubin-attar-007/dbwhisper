/**
 * Typed API client for the DBWhisper natural-language-to-SQL backend.
 *
 * Requests go to the same-origin `/api/*` path, which Next.js rewrites to the backend
 * (see next.config.mjs `rewrites` + the server-side `API_PROXY_TARGET` env var). This keeps
 * the browser same-origin (no CORS) and hides the backend URL. Override with
 * `NEXT_PUBLIC_API_BASE` only if you need to bypass the proxy and call the API directly.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/+$/, "") || "/api";

// ---------------------------------------------------------------------------
// Types matching the backend contract
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  message: string;
  version: string;
}

export type OutputFormat = "json";

export interface QueryRequest {
  query: string;
  db_flag: string;
  output_format: OutputFormat;
  user_id?: string;
  session_id?: string;
  page?: number;
  page_size?: number;
  include_total?: boolean;
}

export interface QueryResultData {
  /** Array of row objects when output_format === "json". */
  results: unknown;
  sql: string;
  row_count: number;
  execution_time_ms: number | null;
  csv: string;
  /** Full result set serialized as a JSON string (table fallback). */
  raw_json: string;
  describe_text: string;
  page: number | null;
  page_size: number | null;
  has_next: boolean | null;
  total_rows: number | null;
}

export interface ExecutionMetadata {
  execution_time_ms: number | null;
  total_rows: number | null;
  retry_count: number;
}

export interface QueryResponse {
  status: "success" | "error";
  sql: string | null;
  validation_passed: boolean | null;
  data: QueryResultData | null;
  error: string | null;
  selected_tables: string[] | null;
  follow_up_questions: string[] | null;
  metadata: ExecutionMetadata;
  natural_summary: string | null;
  token_usage: Record<string, unknown> | null;
}

/** Error thrown when the API responds with a non-2xx HTTP status. */
export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function parseJsonSafe(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function extractErrorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    if (typeof obj.error === "string" && obj.error) return obj.error;
    if (typeof obj.detail === "string" && obj.detail) return obj.detail;
    if (typeof obj.message === "string" && obj.message) return obj.message;
  }
  if (typeof payload === "string" && payload) return payload;
  return fallback;
}

// ---------------------------------------------------------------------------
// API methods
// ---------------------------------------------------------------------------

/** Calls `GET /health`. Throws `ApiError` on a non-2xx response. */
export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/health`, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });

  const payload = await parseJsonSafe(res);

  if (!res.ok) {
    throw new ApiError(
      extractErrorMessage(payload, `Health check failed (HTTP ${res.status})`),
      res.status,
      payload,
    );
  }

  return payload as HealthResponse;
}

/**
 * Calls `POST /query`. On a non-2xx response throws `ApiError` whose message is
 * the server's `error`/`detail` field when available. A 2xx response is returned
 * as-is, even when `status === "error"` (callers inspect that field).
 */
export async function runQuery(
  req: QueryRequest,
  signal?: AbortSignal,
): Promise<QueryResponse> {
  const res = await fetch(`${API_BASE_URL}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(req),
    cache: "no-store",
    signal,
  });

  const payload = await parseJsonSafe(res);

  if (!res.ok) {
    throw new ApiError(
      extractErrorMessage(payload, `Query failed (HTTP ${res.status})`),
      res.status,
      payload,
    );
  }

  return payload as QueryResponse;
}
