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

export interface RunSqlRequest {
  sql: string;
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
  /** Deterministic per-column statistics computed server-side. */
  describe?: Record<string, Record<string, unknown>>;
  describe_text: string;
  /**
   * True when the row cap was reached, so rows exist that you are not looking at. Optional
   * because a v1 backend omits it; `undefined` means "not reported", which is not the same
   * as `false` and must not be rendered as a reassurance.
   */
  truncated?: boolean | null;
  /** The row cap that was applied to this result, when the backend reports one. */
  row_limit?: number | null;
  page: number | null;
  page_size: number | null;
  has_next: boolean | null;
  total_rows: number | null;
}

/** The three decisions the AST policy engine can return (`app/sqlpolicy`). */
export type PolicyDecision = "allow" | "deny" | "needs_approval";

/**
 * What the backend reports about *how* a query was judged and run.
 *
 * The v1 fields (`execution_time_ms`, `total_rows`) are always present. Everything below them
 * arrived with v2 and is optional, so this client keeps working against an older backend — but
 * it also means every consumer must distinguish "reported false" from "not reported at all".
 */
export interface ExecutionMetadata {
  execution_time_ms: number | null;
  total_rows: number | null;
  /** Ruleset that judged the statement, e.g. "sql_policy@2.0.0". */
  policy_version?: string | null;
  policy_decision?: PolicyDecision | string | null;
  /** Literal-independent hash of the statement; approvals bind to this, not to the text. */
  sql_fingerprint?: string | null;
  /** Enrolled tables the statement actually referenced, as resolved from the AST. */
  tables_used?: string[] | null;
  /** Mirrors `QueryResultData.truncated` for callers that only read metadata. */
  truncated?: boolean | null;
  /**
   * Whether the database itself opened the session read-only. True on PostgreSQL, MySQL and
   * SQLite; false on SQL Server, which has no session-level read-only mode.
   */
  read_only_enforced?: boolean | null;
  /** Machine-readable failure class (policy, timeout, syntax, ...). */
  error_category?: string | null;
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
}

export interface DatabaseSummary {
  db_flag: string;
  db_type: string;
  description: string | null;
  is_public: boolean;
}

export interface VerifiedPair {
  id: number;
  db_flag: string;
  question: string;
  sql: string;
  created_at: string | null;
}

export interface SchemaTable {
  table: string;
  schema_name: string;
  columns: string[];
  primary_key: string[];
  has_foreign_keys: boolean;
  description: string | null;
}

export interface SchemaResponse {
  db_flag: string;
  database_name: string | null;
  tables: SchemaTable[];
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
    credentials: "include", // carry the session cookie so owned databases are reachable
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

/** Calls `GET /databases`. Returns the enrolled databases the caller may query. */
export async function listDatabases(signal?: AbortSignal): Promise<DatabaseSummary[]> {
  const res = await fetch(`${API_BASE_URL}/databases`, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (!res.ok) {
    throw new ApiError(`Failed to list databases (HTTP ${res.status})`, res.status, null);
  }
  const payload = (await parseJsonSafe(res)) as { databases?: DatabaseSummary[] } | null;
  return payload?.databases ?? [];
}

/**
 * Calls `POST /run_sql` — executes user-edited SQL through the same read-only validator +
 * executor as generated SQL (no LLM). A 2xx is returned as-is even when `status === "error"`.
 */
export async function runSql(
  req: RunSqlRequest,
  signal?: AbortSignal,
): Promise<QueryResponse> {
  const res = await fetch(`${API_BASE_URL}/run_sql`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(req),
    credentials: "include",
    cache: "no-store",
    signal,
  });
  const payload = await parseJsonSafe(res);
  if (!res.ok) {
    throw new ApiError(
      extractErrorMessage(payload, `Run SQL failed (HTTP ${res.status})`),
      res.status,
      payload,
    );
  }
  return payload as QueryResponse;
}

/** Verified Q→SQL library (the flywheel). */
export async function listVerifiedPairs(
  dbFlag?: string,
  signal?: AbortSignal,
): Promise<VerifiedPair[]> {
  const qs = dbFlag ? `?db_flag=${encodeURIComponent(dbFlag)}` : "";
  const res = await fetch(`${API_BASE_URL}/training/pairs${qs}`, {
    headers: { Accept: "application/json" },
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (!res.ok) {
    throw new ApiError(`Failed to load verified queries (HTTP ${res.status})`, res.status, null);
  }
  const payload = (await parseJsonSafe(res)) as { pairs?: VerifiedPair[] } | null;
  return payload?.pairs ?? [];
}

export async function saveVerifiedPair(
  input: { db_flag: string; question: string; sql: string },
  signal?: AbortSignal,
): Promise<VerifiedPair> {
  const res = await fetch(`${API_BASE_URL}/training/pairs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
    credentials: "include",
    cache: "no-store",
    signal,
  });
  const payload = await parseJsonSafe(res);
  if (!res.ok) {
    throw new ApiError(
      extractErrorMessage(payload, `Failed to save (HTTP ${res.status})`),
      res.status,
      payload,
    );
  }
  return payload as VerifiedPair;
}

export async function deleteVerifiedPair(id: number, signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/training/pairs/${id}`, {
    method: "DELETE",
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (!res.ok) {
    throw new ApiError(`Failed to delete (HTTP ${res.status})`, res.status, null);
  }
}

/** Calls `GET /schemas/{db_flag}` — the enrolled tables/columns for the schema browser. */
export async function getSchema(dbFlag: string, signal?: AbortSignal): Promise<SchemaResponse> {
  const res = await fetch(`${API_BASE_URL}/schemas/${encodeURIComponent(dbFlag)}`, {
    headers: { Accept: "application/json" },
    credentials: "include",
    cache: "no-store",
    signal,
  });
  const payload = await parseJsonSafe(res);
  if (!res.ok) {
    throw new ApiError(
      extractErrorMessage(payload, `Failed to load schema (HTTP ${res.status})`),
      res.status,
      payload,
    );
  }
  return payload as SchemaResponse;
}
