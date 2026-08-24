/**
 * History is stored in a place the user can edit and the browser can refuse to write. These tests
 * therefore cover the hostile cases as well as the happy path: garbage in localStorage, a quota
 * error on write, and the dedup/cap rules that stop the menu growing without bound.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addHistory, clearHistory, loadHistory } from "@/src/lib/history";

const KEY = "dbwhisper.history.v1";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("loadHistory", () => {
  it("returns an empty list when nothing is stored", () => {
    expect(loadHistory()).toEqual([]);
  });

  it("returns an empty list rather than throwing on malformed JSON", () => {
    window.localStorage.setItem(KEY, "{not json");
    expect(loadHistory()).toEqual([]);
  });

  it("returns an empty list when the stored value is not an array", () => {
    window.localStorage.setItem(KEY, JSON.stringify({ query: "x" }));
    expect(loadHistory()).toEqual([]);
  });

  it("drops entries that do not match the shape but keeps the valid ones", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify([
        { query: "good", dbFlag: "demo", ts: 1 },
        { query: "missing ts", dbFlag: "demo" },
        { query: 42, dbFlag: "demo", ts: 2 },
        null,
      ]),
    );
    expect(loadHistory()).toEqual([{ query: "good", dbFlag: "demo", ts: 1 }]);
  });
});

describe("addHistory", () => {
  it("prepends the newest entry and persists it", () => {
    addHistory({ query: "first", dbFlag: "demo", ts: 1 });
    const result = addHistory({ query: "second", dbFlag: "demo", ts: 2 });

    expect(result.map((e) => e.query)).toEqual(["second", "first"]);
    expect(loadHistory().map((e) => e.query)).toEqual(["second", "first"]);
  });

  it("dedupes on query + dbFlag, moving the repeat to the front", () => {
    addHistory({ query: "a", dbFlag: "demo", ts: 1 });
    addHistory({ query: "b", dbFlag: "demo", ts: 2 });
    const result = addHistory({ query: "a", dbFlag: "demo", ts: 3 });

    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({ query: "a", dbFlag: "demo", ts: 3 });
  });

  it("keeps the same question against a different database as a separate entry", () => {
    addHistory({ query: "a", dbFlag: "demo", ts: 1 });
    const result = addHistory({ query: "a", dbFlag: "crm_db", ts: 2 });
    expect(result).toHaveLength(2);
  });

  it("caps the list at 20 entries, dropping the oldest", () => {
    for (let i = 0; i < 25; i += 1) {
      addHistory({ query: `q${i}`, dbFlag: "demo", ts: i });
    }
    const result = loadHistory();
    expect(result).toHaveLength(20);
    expect(result[0].query).toBe("q24");
    expect(result.at(-1)?.query).toBe("q5");
  });

  it("still returns the updated list when the write is rejected", () => {
    addHistory({ query: "kept", dbFlag: "demo", ts: 1 });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });

    const result = addHistory({ query: "unwritable", dbFlag: "demo", ts: 2 });
    expect(result.map((e) => e.query)).toEqual(["unwritable", "kept"]);
    // The write failed, so what is on disk is still the previous state.
    vi.restoreAllMocks();
    expect(loadHistory().map((e) => e.query)).toEqual(["kept"]);
  });
});

describe("clearHistory", () => {
  it("removes everything", () => {
    addHistory({ query: "a", dbFlag: "demo", ts: 1 });
    clearHistory();
    expect(loadHistory()).toEqual([]);
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("does not throw when storage rejects the removal", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("SecurityError");
    });
    expect(() => clearHistory()).not.toThrow();
  });
});
