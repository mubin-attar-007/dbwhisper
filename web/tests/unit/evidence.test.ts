/**
 * The point of these tests is the distinction the module exists for: "the backend said no" and
 * "the backend said nothing" must never collapse into the same chip. Each mechanism therefore gets
 * three cases — reported true, reported false, absent — and the absent case asserts the `unknown`
 * state explicitly rather than just checking that nothing crashed.
 */
import { describe, expect, it } from "vitest";
import {
  buildEvidence,
  resolveTablesUsed,
  resolveTruncation,
  shortFingerprint,
} from "@/src/lib/evidence";
import { makeData, makeResponse } from "./fixtures";

function itemFor(response: Parameters<typeof buildEvidence>[0], id: string) {
  return buildEvidence(response).find((i) => i.id === id);
}

describe("policy evidence", () => {
  it("reports an allow decision with its ruleset version", () => {
    const item = itemFor(
      makeResponse({}, { policy_decision: "allow", policy_version: "sql_policy@2.0.0" }),
      "policy",
    );
    expect(item?.state).toBe("ok");
    expect(item?.label).toBe("Policy: allow");
    expect(item?.detail).toContain("sql_policy@2.0.0");
  });

  it("reports a deny as a refusal, not a warning", () => {
    const item = itemFor(makeResponse({}, { policy_decision: "deny" }), "policy");
    expect(item?.state).toBe("risk");
  });

  it("reports needs_approval as held for a human", () => {
    const item = itemFor(makeResponse({}, { policy_decision: "needs_approval" }), "policy");
    expect(item?.state).toBe("warn");
    expect(item?.label).toBe("Policy: needs approval");
  });

  it("falls back to the v1 boolean and says the version is missing", () => {
    const item = itemFor(makeResponse({ validation_passed: true }), "policy");
    expect(item?.state).toBe("ok");
    expect(item?.label).toBe("Validation passed");
    expect(item?.detail).toContain("no policy decision");
  });

  it("is unknown — not ok — when neither field is present", () => {
    const item = itemFor(makeResponse(), "policy");
    expect(item?.state).toBe("unknown");
    expect(item?.label).toBe("Policy: not reported");
  });
});

describe("read-only evidence", () => {
  it("is ok when the database enforced the session", () => {
    const item = itemFor(makeResponse({}, { read_only_enforced: true }), "read-only");
    expect(item?.state).toBe("ok");
  });

  it("is informational — not an error — when the engine has no read-only session", () => {
    const item = itemFor(makeResponse({}, { read_only_enforced: false }), "read-only");
    expect(item?.state).toBe("info");
    expect(item?.detail).toContain("SQL Server");
  });

  it("is unknown when the backend did not report it", () => {
    expect(itemFor(makeResponse(), "read-only")?.state).toBe("unknown");
  });
});

describe("schema grounding evidence", () => {
  it("counts the tables the executor resolved", () => {
    const item = itemFor(
      makeResponse({}, { tables_used: ["orders", "order_items"] }),
      "grounding",
    );
    expect(item?.state).toBe("ok");
    expect(item?.label).toBe("Schema grounded · 2 tables");
    expect(item?.detail).toContain("orders, order_items");
  });

  it("uses the singular for one table", () => {
    expect(itemFor(makeResponse({}, { tables_used: ["orders"] }), "grounding")?.label).toBe(
      "Schema grounded · 1 table",
    );
  });

  it("falls back to the v1 selected_tables", () => {
    const item = itemFor(makeResponse({ selected_tables: ["customers"] }), "grounding");
    expect(item?.state).toBe("ok");
    expect(item?.label).toContain("1 table");
  });

  it("prefers metadata.tables_used over selected_tables when both are present", () => {
    const response = makeResponse({ selected_tables: ["stale"] }, { tables_used: ["actual"] });
    expect(itemFor(response, "grounding")?.detail).toContain("actual");
  });

  it("is unknown when neither is present", () => {
    expect(itemFor(makeResponse(), "grounding")?.state).toBe("unknown");
  });
});

describe("resolveTruncation", () => {
  it("reads the flag from the result envelope", () => {
    const response = makeResponse({ data: makeData([], { truncated: true, row_limit: 1000 }) });
    expect(resolveTruncation(response)).toEqual({
      truncated: true,
      rowLimit: 1000,
      reported: true,
    });
  });

  it("falls back to the metadata mirror of the flag", () => {
    const response = makeResponse({ data: makeData([]) }, { truncated: true });
    expect(resolveTruncation(response).truncated).toBe(true);
  });

  it("marks an absent flag as not reported, distinct from a reported false", () => {
    expect(resolveTruncation(makeResponse({ data: makeData([]) })).reported).toBe(false);
    expect(
      resolveTruncation(makeResponse({ data: makeData([], { truncated: false }) })).reported,
    ).toBe(true);
  });

  it("ignores a non-finite row limit", () => {
    const response = makeResponse({
      data: makeData([], { truncated: true, row_limit: Number.NaN }),
    });
    expect(resolveTruncation(response).rowLimit).toBeNull();
  });
});

describe("truncation evidence chip", () => {
  it("warns and names the cap when truncated", () => {
    const item = itemFor(
      makeResponse({ data: makeData([], { truncated: true, row_limit: 1000 }) }),
      "truncated",
    );
    expect(item?.state).toBe("warn");
    expect(item?.detail).toContain("1,000");
  });

  it("confirms completeness when the flag is reported false", () => {
    const item = itemFor(
      makeResponse({ data: makeData([], { truncated: false }) }),
      "truncated",
    );
    expect(item?.state).toBe("ok");
    expect(item?.label).toBe("Complete result");
  });

  it("emits no chip at all when truncation was never reported", () => {
    expect(itemFor(makeResponse({ data: makeData([]) }), "truncated")).toBeUndefined();
  });
});

describe("fingerprint and error category", () => {
  it("shortens a long fingerprint but keeps the full value in the detail", () => {
    const full = "a".repeat(64);
    const item = itemFor(makeResponse({}, { sql_fingerprint: full }), "fingerprint");
    expect(item?.label).toBe(`${"a".repeat(12)}…`);
    expect(item?.detail).toContain(full);
  });

  it("leaves a short fingerprint intact", () => {
    expect(shortFingerprint("abc123")).toBe("abc123");
  });

  it("treats a blank or missing fingerprint as absent", () => {
    expect(shortFingerprint("   ")).toBeNull();
    expect(shortFingerprint(null)).toBeNull();
    expect(itemFor(makeResponse(), "fingerprint")).toBeUndefined();
  });

  it("surfaces an error category as a refusal chip", () => {
    const item = itemFor(makeResponse({}, { error_category: "policy" }), "error-category");
    expect(item?.state).toBe("risk");
    expect(item?.label).toBe("Failure: policy");
  });

  it("omits the error chip on a clean run", () => {
    expect(itemFor(makeResponse(), "error-category")).toBeUndefined();
  });
});

describe("buildEvidence ordering", () => {
  it("reads grounding, then policy, then execution, then completeness", () => {
    const response = makeResponse(
      { data: makeData([], { truncated: false }) },
      {
        tables_used: ["orders"],
        policy_decision: "allow",
        read_only_enforced: true,
        sql_fingerprint: "deadbeefcafebabe",
      },
    );
    expect(buildEvidence(response).map((i) => i.id)).toEqual([
      "grounding",
      "policy",
      "read-only",
      "truncated",
      "fingerprint",
    ]);
  });

  it("still returns the three core chips for a bare v1 response", () => {
    expect(buildEvidence(makeResponse()).map((i) => i.id)).toEqual([
      "grounding",
      "policy",
      "read-only",
    ]);
  });
});

describe("resolveTablesUsed", () => {
  it("ignores an empty array in metadata and falls through", () => {
    const response = makeResponse({ selected_tables: ["customers"] }, { tables_used: [] });
    expect(resolveTablesUsed(response)).toEqual(["customers"]);
  });

  it("returns an empty array when nothing is reported", () => {
    expect(resolveTablesUsed(makeResponse())).toEqual([]);
  });
});
