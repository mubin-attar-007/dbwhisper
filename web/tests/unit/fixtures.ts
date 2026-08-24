/**
 * Response builders for the unit suite.
 *
 * These mirror the backend contract rather than the frontend's convenience: every v2 metadata field
 * is optional there, so the builders default them to *absent* instead of to a benign value. A test
 * that wants "read-only enforced" has to say so, which is what makes the "not reported" paths in
 * `src/lib/evidence.ts` genuinely exercised rather than accidentally skipped.
 */
import type {
  ExecutionMetadata,
  QueryResponse,
  QueryResultData,
} from "@/src/lib/api";
import type { Row } from "@/src/lib/rows";

export function makeData(
  rows: Row[],
  overrides: Partial<QueryResultData> = {},
): QueryResultData {
  return {
    results: rows,
    sql: "SELECT 1",
    row_count: rows.length,
    execution_time_ms: 12.5,
    csv: "",
    raw_json: JSON.stringify(rows),
    describe_text: "",
    page: null,
    page_size: null,
    has_next: null,
    total_rows: null,
    ...overrides,
  };
}

export function makeResponse(
  overrides: Partial<QueryResponse> = {},
  metadata: Partial<ExecutionMetadata> = {},
): QueryResponse {
  return {
    status: "success",
    sql: "SELECT 1",
    validation_passed: null,
    data: null,
    error: null,
    selected_tables: null,
    follow_up_questions: null,
    natural_summary: null,
    ...overrides,
    metadata: {
      execution_time_ms: 12.5,
      total_rows: null,
      ...metadata,
    },
  };
}
