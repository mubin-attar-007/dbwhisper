/**
 * Turns the backend's execution metadata into the short list of claims the UI is willing to make
 * about a result.
 *
 * The interesting decision here is the three-valued logic. Every v2 metadata field is optional, so
 * a missing `read_only_enforced` and a `read_only_enforced: false` mean completely different
 * things: the first is "the backend did not tell us", the second is "the database did not enforce
 * it". Rendering both as a grey chip would be sloppy; rendering the first as a green tick would be
 * a lie. `EvidenceState.UNKNOWN` exists so the UI can say "not reported" out loud, which is the
 * only honest option when the answer is genuinely unknown.
 *
 * Keeping this as a pure function (rather than branching inside JSX) is what makes the honesty
 * testable — see `tests/unit/evidence.test.ts`.
 */
import type { QueryResponse, QueryResultData } from "@/src/lib/api";

export type EvidenceState =
  /** The mechanism ran and reported the good outcome. */
  | "ok"
  /** The mechanism ran and reported something the reader needs to act on. */
  | "warn"
  /** The mechanism ran and refused. */
  | "risk"
  /** Neutral fact, neither good nor bad. */
  | "info"
  /** The backend did not report it. Never render this as reassurance. */
  | "unknown";

export interface EvidenceItem {
  /** Stable key; also used by tests and by `data-evidence` in the DOM. */
  id: string;
  label: string;
  /** One clause explaining what the label is asserting, shown as the chip's tooltip/description. */
  detail: string;
  state: EvidenceState;
}

/** Truncation as the UI needs it: whether to warn, and the cap to name if we know it. */
export interface TruncationInfo {
  truncated: boolean;
  rowLimit: number | null;
  /** False when neither the result envelope nor the metadata carried a `truncated` field. */
  reported: boolean;
}

function firstBoolean(...values: (boolean | null | undefined)[]): boolean | null {
  for (const v of values) {
    if (typeof v === "boolean") return v;
  }
  return null;
}

/**
 * Resolves truncation from the two places the backend reports it. `QueryResultData.truncated` is
 * authoritative; `ExecutionMetadata.truncated` mirrors it for callers that only read metadata.
 */
export function resolveTruncation(response: QueryResponse): TruncationInfo {
  const data: QueryResultData | null = response.data;
  const flag = firstBoolean(data?.truncated, response.metadata?.truncated);
  const rowLimit =
    typeof data?.row_limit === "number" && Number.isFinite(data.row_limit)
      ? data.row_limit
      : null;
  return { truncated: flag === true, rowLimit, reported: flag !== null };
}

/** Normalises the tables the statement touched: metadata first, then the v1 `selected_tables`. */
export function resolveTablesUsed(response: QueryResponse): string[] {
  const fromMetadata = response.metadata?.tables_used;
  if (Array.isArray(fromMetadata) && fromMetadata.length > 0) return fromMetadata;
  const selected = response.selected_tables;
  if (Array.isArray(selected) && selected.length > 0) return selected;
  return [];
}

/** Short, comparable form of the statement fingerprint. The full value stays in the tooltip. */
export function shortFingerprint(fingerprint: string | null | undefined): string | null {
  if (typeof fingerprint !== "string") return null;
  const trimmed = fingerprint.trim();
  if (!trimmed) return null;
  return trimmed.length <= 12 ? trimmed : `${trimmed.slice(0, 12)}…`;
}

function policyItem(response: QueryResponse): EvidenceItem {
  const decision = response.metadata?.policy_decision ?? null;
  const version = response.metadata?.policy_version ?? null;
  const versionSuffix = version ? ` by ${version}` : "";

  if (decision === "allow") {
    return {
      id: "policy",
      label: "Policy: allow",
      detail: `Admitted as a single read-only statement${versionSuffix}.`,
      state: "ok",
    };
  }
  if (decision === "deny") {
    return {
      id: "policy",
      label: "Policy: deny",
      detail: `Refused before execution${versionSuffix}.`,
      state: "risk",
    };
  }
  if (decision === "needs_approval") {
    return {
      id: "policy",
      label: "Policy: needs approval",
      detail: `Held for a human decision${versionSuffix}.`,
      state: "warn",
    };
  }
  if (typeof decision === "string" && decision.trim()) {
    return {
      id: "policy",
      label: `Policy: ${decision}`,
      detail: `Reported by the policy engine${versionSuffix}.`,
      state: "info",
    };
  }

  // No decision field: fall back to the v1 boolean, which says less but is not nothing.
  if (response.validation_passed === true) {
    return {
      id: "policy",
      label: "Validation passed",
      detail: "The backend reported a passing check but no policy decision or version.",
      state: "ok",
    };
  }
  if (response.validation_passed === false) {
    return {
      id: "policy",
      label: "Validation failed",
      detail: "The backend reported a failing check but no policy decision or version.",
      state: "risk",
    };
  }
  return {
    id: "policy",
    label: "Policy: not reported",
    detail: "This backend did not return a policy decision for the statement.",
    state: "unknown",
  };
}

function readOnlyItem(response: QueryResponse): EvidenceItem {
  const enforced = response.metadata?.read_only_enforced;
  if (enforced === true) {
    return {
      id: "read-only",
      label: "Read-only session",
      detail: "The database itself opened this transaction read-only, and it was rolled back.",
      state: "ok",
    };
  }
  if (enforced === false) {
    return {
      id: "read-only",
      label: "Read-only: not at the database",
      detail:
        "This engine has no session-level read-only mode (SQL Server), so the controls here are the policy engine, a least-privilege login and the query timeout.",
      state: "info",
    };
  }
  return {
    id: "read-only",
    label: "Read-only session: not reported",
    detail: "This backend did not report whether the database enforced a read-only session.",
    state: "unknown",
  };
}

function groundingItem(response: QueryResponse): EvidenceItem {
  const tables = resolveTablesUsed(response);
  if (tables.length === 0) {
    return {
      id: "grounding",
      label: "Tables: not reported",
      detail: "This backend did not report which enrolled tables the statement referenced.",
      state: "unknown",
    };
  }
  const noun = tables.length === 1 ? "table" : "tables";
  return {
    id: "grounding",
    label: `Schema grounded · ${tables.length} ${noun}`,
    detail: `The statement referenced: ${tables.join(", ")}.`,
    state: "ok",
  };
}

function truncationItem(response: QueryResponse): EvidenceItem | null {
  const { truncated, rowLimit, reported } = resolveTruncation(response);
  if (!reported) return null; // Silence beats an "unknown" chip for a field most backends omit.
  if (truncated) {
    return {
      id: "truncated",
      label: "Result truncated",
      detail: rowLimit
        ? `The row cap of ${rowLimit.toLocaleString()} was reached, so more rows exist than are shown.`
        : "The row cap was reached, so more rows exist than are shown.",
      state: "warn",
    };
  }
  return {
    id: "truncated",
    label: "Complete result",
    detail: rowLimit
      ? `Every matching row is shown; the row cap of ${rowLimit.toLocaleString()} was not reached.`
      : "Every matching row is shown; the row cap was not reached.",
    state: "ok",
  };
}

function fingerprintItem(response: QueryResponse): EvidenceItem | null {
  const short = shortFingerprint(response.metadata?.sql_fingerprint);
  if (!short) return null;
  return {
    id: "fingerprint",
    label: short,
    detail: `Statement fingerprint ${response.metadata?.sql_fingerprint} — literal-independent, so the same shape of query fingerprints the same way.`,
    state: "info",
  };
}

function errorCategoryItem(response: QueryResponse): EvidenceItem | null {
  const category = response.metadata?.error_category;
  if (typeof category !== "string" || !category.trim()) return null;
  return {
    id: "error-category",
    label: `Failure: ${category}`,
    detail: "Machine-readable failure class reported by the backend.",
    state: "risk",
  };
}

/**
 * The evidence strip, in reading order: what it was grounded in, how it was judged, how it ran,
 * how complete the answer is, and what identifies the statement.
 */
export function buildEvidence(response: QueryResponse): EvidenceItem[] {
  return [
    groundingItem(response),
    policyItem(response),
    readOnlyItem(response),
    truncationItem(response),
    errorCategoryItem(response),
    fingerprintItem(response),
  ].filter((item): item is EvidenceItem => item !== null);
}
