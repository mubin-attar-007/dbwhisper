/**
 * `pickChart` decides whether a result set gets a chart at all. The failure mode worth testing is
 * not "picks the wrong chart" but "charts something that should not be charted" — a chart implies
 * a relationship, and drawing one over ids or free text invents meaning that is not in the data.
 * So most of these cases assert `null`.
 */
import { describe, expect, it } from "vitest";
import { pickChart } from "@/src/lib/chart";

describe("pickChart — refusals", () => {
  it("returns null for no rows and for no columns", () => {
    expect(pickChart([], ["a"])).toBeNull();
    expect(pickChart([{ a: 1 }], [])).toBeNull();
  });

  it("returns null when no column is numeric", () => {
    const rows = [{ city: "Mumbai" }, { city: "Pune" }];
    expect(pickChart(rows, ["city"])).toBeNull();
  });

  it("returns null for a purely numeric table with no category to plot against", () => {
    const rows = [{ a: 1, b: 2 }, { a: 3, b: 4 }];
    expect(pickChart(rows, ["a", "b"])).toBeNull();
  });

  it("returns null when there are too many categories to read", () => {
    const rows = Array.from({ length: 31 }, (_, i) => ({ label: `c${i}`, n: i }));
    expect(pickChart(rows, ["label", "n"])).toBeNull();
  });
});

describe("pickChart — KPI", () => {
  it("treats a single row with a single measure as a headline number", () => {
    expect(pickChart([{ total: 42 }], ["total"])).toEqual({
      kind: "kpi",
      label: "total",
      value: 42,
    });
  });

  it("parses a formatted currency string into the KPI value", () => {
    expect(pickChart([{ revenue: "$1,234" }], ["revenue"])).toEqual({
      kind: "kpi",
      label: "revenue",
      value: 1234,
    });
  });
});

describe("pickChart — bar", () => {
  it("plots a measure against the first categorical column", () => {
    const rows = [
      { city: "Mumbai", customers: 3 },
      { city: "Pune", customers: 1 },
    ];
    expect(pickChart(rows, ["city", "customers"])).toEqual({
      kind: "bar",
      x: "city",
      y: "customers",
      points: [
        { label: "Mumbai", value: 3 },
        { label: "Pune", value: 1 },
      ],
    });
  });

  it("substitutes 0 for a value that cannot be read as a number", () => {
    const rows = [
      { city: "Mumbai", n: 3 },
      { city: "Pune", n: null },
    ];
    const spec = pickChart(rows, ["city", "n"]);
    expect(spec).not.toBeNull();
    expect(spec && spec.kind === "bar" && spec.points[1]).toEqual({ label: "Pune", value: 0 });
  });
});

describe("pickChart — line", () => {
  it("plots a date-like column as a time series", () => {
    const rows = [
      { order_date: "2026-01-01", revenue: 10 },
      { order_date: "2026-01-02", revenue: 20 },
    ];
    expect(pickChart(rows, ["order_date", "revenue"])).toMatchObject({
      kind: "line",
      x: "order_date",
      y: "revenue",
    });
  });

  it("does not read a numeric year as a date axis", () => {
    // `year` matches the date hint by name, but the values are numbers, so it is a measure and the
    // result has no category left to plot against.
    const rows = [
      { year: 2024, revenue: 10 },
      { year: 2025, revenue: 20 },
    ];
    expect(pickChart(rows, ["year", "revenue"])).toBeNull();
  });

  it("ignores a column whose name has no temporal hint even if it parses as a date", () => {
    const rows = [
      { label: "2026-01-01", revenue: 10 },
      { label: "2026-01-02", revenue: 20 },
    ];
    expect(pickChart(rows, ["label", "revenue"])).toMatchObject({ kind: "bar" });
  });
});
