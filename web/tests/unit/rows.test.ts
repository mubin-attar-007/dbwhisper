/**
 * `resolveRows` is the seam between a backend that may serialize results three different ways and
 * a table that can only render one. Its whole job is to fail soft, so these tests are mostly about
 * the bad inputs: wrong shape, malformed JSON, wrapped payloads.
 */
import { describe, expect, it } from "vitest";
import { deriveColumns, formatCell, resolveRows } from "@/src/lib/rows";
import { makeData } from "./fixtures";

describe("resolveRows", () => {
  it("returns an empty array for null or undefined data", () => {
    expect(resolveRows(null)).toEqual([]);
    expect(resolveRows(undefined)).toEqual([]);
  });

  it("prefers `results` when it is an array of plain objects", () => {
    const rows = [{ a: 1 }, { a: 2 }];
    expect(resolveRows(makeData(rows, { raw_json: "[]" }))).toEqual(rows);
  });

  it("treats an array of non-objects as untabular and falls through to raw_json", () => {
    const data = makeData([], {
      results: [1, 2, 3],
      raw_json: JSON.stringify([{ n: 1 }]),
    });
    expect(resolveRows(data)).toEqual([{ n: 1 }]);
  });

  it("rejects arrays of arrays (a positional result set is not a row object)", () => {
    const data = makeData([], { results: [["a", 1]], raw_json: "" });
    expect(resolveRows(data)).toEqual([]);
  });

  it("unwraps a raw_json payload nested under a `results` key", () => {
    const data = makeData([], {
      results: null,
      raw_json: JSON.stringify({ results: [{ city: "Mumbai" }] }),
    });
    expect(resolveRows(data)).toEqual([{ city: "Mumbai" }]);
  });

  it("returns an empty array rather than throwing on malformed raw_json", () => {
    const data = makeData([], { results: null, raw_json: "{not json" });
    expect(resolveRows(data)).toEqual([]);
  });

  it("does not treat a null element as a row object", () => {
    const data = makeData([], { results: [{ a: 1 }, null], raw_json: "" });
    expect(resolveRows(data)).toEqual([]);
  });
});

describe("deriveColumns", () => {
  it("returns keys in first-seen order", () => {
    expect(deriveColumns([{ b: 1, a: 2 }])).toEqual(["b", "a"]);
  });

  it("unions keys across ragged rows without duplicating", () => {
    const rows = [{ a: 1 }, { a: 2, b: 3 }, { c: 4, a: 5 }];
    expect(deriveColumns(rows)).toEqual(["a", "b", "c"]);
  });

  it("returns an empty list for no rows", () => {
    expect(deriveColumns([])).toEqual([]);
  });
});

describe("formatCell", () => {
  it("renders null and undefined as an empty string, not as the word 'null'", () => {
    expect(formatCell(null)).toBe("");
    expect(formatCell(undefined)).toBe("");
  });

  it("keeps falsy scalars visible", () => {
    expect(formatCell(0)).toBe("0");
    expect(formatCell(false)).toBe("false");
    expect(formatCell("")).toBe("");
  });

  it("serializes objects and arrays as JSON", () => {
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
    expect(formatCell([1, 2])).toBe("[1,2]");
  });

  it("falls back to String() when JSON.stringify throws on a cycle", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(formatCell(cyclic)).toBe("[object Object]");
  });
});
