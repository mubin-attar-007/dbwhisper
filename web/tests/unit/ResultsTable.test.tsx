/**
 * The results table is where a formatting or sorting bug turns into a wrong answer on screen, so
 * these tests assert what a user would actually read: the order of the cells after a sort, the
 * `aria-sort` state a screen reader announces, and that a null renders as an empty cell rather
 * than the word "null".
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ResultsTable } from "@/app/components/ResultsTable";
import { makeData } from "./fixtures";

const ROWS = [
  { city: "Pune", customers: 9 },
  { city: "Mumbai", customers: 10 },
  { city: "Delhi", customers: 2 },
];

function columnValues(name: string): string[] {
  const table = screen.getByRole("table");
  const headers = within(table).getAllByRole("columnheader");
  const index = headers.findIndex((h) => h.textContent?.startsWith(name));
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[index]?.textContent ?? "");
}

describe("ResultsTable", () => {
  it("renders the row count, execution time and every row", () => {
    render(<ResultsTable data={makeData(ROWS, { execution_time_ms: 42 })} />);

    const region = screen.getByRole("region", { name: "Query results" });
    expect(region).toHaveTextContent("3");
    expect(region).toHaveTextContent("42 ms");
    expect(screen.getAllByRole("row")).toHaveLength(4); // header + 3
  });

  it("shows an em dash when the backend reported no execution time", () => {
    render(<ResultsTable data={makeData(ROWS, { execution_time_ms: null })} />);
    expect(screen.getByRole("region", { name: "Query results" })).toHaveTextContent("—");
  });

  it("renders an empty state instead of an empty table", () => {
    render(<ResultsTable data={makeData([])} />);
    expect(screen.getByText("No rows to display.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sorts numerically ascending, then descending, then returns to source order", async () => {
    const user = userEvent.setup();
    render(<ResultsTable data={makeData(ROWS)} />);
    const header = screen.getByRole("button", { name: "Sort by customers" });

    expect(columnValues("customers")).toEqual(["9", "10", "2"]);

    await user.click(header);
    expect(columnValues("customers")).toEqual(["2", "9", "10"]);

    await user.click(header);
    expect(columnValues("customers")).toEqual(["10", "9", "2"]);

    await user.click(header);
    expect(columnValues("customers")).toEqual(["9", "10", "2"]);
  });

  it("keeps aria-sort in step with the visible order", async () => {
    const user = userEvent.setup();
    render(<ResultsTable data={makeData(ROWS)} />);
    const cityHeader = screen.getByRole("columnheader", { name: /city/ });

    expect(cityHeader).toHaveAttribute("aria-sort", "none");
    await user.click(within(cityHeader).getByRole("button"));
    expect(cityHeader).toHaveAttribute("aria-sort", "ascending");
    await user.click(within(cityHeader).getByRole("button"));
    expect(cityHeader).toHaveAttribute("aria-sort", "descending");
  });

  it("sorts strings case-sensitively by locale, not by JSON order", async () => {
    const user = userEvent.setup();
    render(<ResultsTable data={makeData(ROWS)} />);

    await user.click(screen.getByRole("button", { name: "Sort by city" }));
    expect(columnValues("city")).toEqual(["Delhi", "Mumbai", "Pune"]);
  });

  it("is reachable and sortable from the keyboard alone", async () => {
    const user = userEvent.setup();
    render(<ResultsTable data={makeData(ROWS)} />);

    await user.tab(); // Export CSV
    await user.tab(); // Export JSON
    await user.tab(); // Copy Markdown
    await user.tab(); // first sortable column header
    expect(screen.getByRole("button", { name: "Sort by city" })).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(columnValues("city")).toEqual(["Delhi", "Mumbai", "Pune"]);
  });

  it("unions ragged rows into one column set and leaves missing cells blank", () => {
    render(<ResultsTable data={makeData([{ a: 1 }, { b: 2 }])} />);

    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(columnValues("a")).toEqual(["1", ""]);
    expect(columnValues("b")).toEqual(["", "2"]);
  });

  it("renders null as an empty cell rather than the string 'null'", () => {
    render(<ResultsTable data={makeData([{ city: "Pune", customers: null }])} />);
    expect(columnValues("customers")).toEqual([""]);
  });

  it("falls back to raw_json when `results` is not a row array", () => {
    const data = makeData([], {
      results: "not rows",
      raw_json: JSON.stringify([{ city: "Nagpur" }]),
      row_count: 1,
    });
    render(<ResultsTable data={data} />);
    expect(columnValues("city")).toEqual(["Nagpur"]);
  });

  it("hides the export controls when there is nothing to export", () => {
    render(<ResultsTable data={makeData([])} />);
    expect(screen.queryByRole("button", { name: "CSV" })).not.toBeInTheDocument();
  });
});
