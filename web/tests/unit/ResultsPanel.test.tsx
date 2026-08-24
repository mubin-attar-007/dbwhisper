/**
 * The results panel is the assembly point: summary, evidence, truncation warning, chart, table and
 * the SQL itself. These tests check the assembly and the ordering guarantee that matters — the
 * truncation warning must precede the table, because a warning underneath a table has already
 * failed at its job.
 *
 * `@/src/lib/api` is mocked to keep the suite offline. Nothing here asserts that a mock was called;
 * the assertions are all about what a reader sees.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResultsPanel } from "@/app/components/ResultsPanel";
import { WorkspaceProvider } from "@/app/components/WorkspaceProvider";
import { makeData, makeResponse } from "./fixtures";

vi.mock("@/src/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/src/lib/api")>();
  return {
    ...actual,
    // Never settles: the provider's database list is irrelevant here, and a promise that resolves
    // after render would land a state update outside act() and drown the run in warnings.
    listDatabases: vi.fn(() => new Promise<never>(() => undefined)),
    runQuery: vi.fn(),
    runSql: vi.fn(),
    saveVerifiedPair: vi.fn(async () => ({
      id: 1,
      db_flag: "demo",
      question: "q",
      sql: "SELECT 1",
      created_at: null,
    })),
  };
});

function renderPanel(response: Parameters<typeof ResultsPanel>[0]["response"], onRerun?: (sql: string) => void) {
  return render(
    <WorkspaceProvider>
      <ResultsPanel response={response} onFollowUp={() => undefined} onRerunSql={onRerun} />
    </WorkspaceProvider>,
  );
}

const ROWS = [
  { city: "Mumbai", customers: 10 },
  { city: "Pune", customers: 9 },
];

describe("ResultsPanel", () => {
  it("leads with the plain-language summary", () => {
    renderPanel(
      makeResponse({
        natural_summary: "Mumbai has the most customers.",
        data: makeData(ROWS),
      }),
    );
    expect(screen.getByText("Mumbai has the most customers.")).toBeInTheDocument();
  });

  it("shows the evidence strip above the results", () => {
    renderPanel(
      makeResponse(
        { data: makeData(ROWS) },
        {
          tables_used: ["customers"],
          policy_decision: "allow",
          policy_version: "sql_policy@2.0.0",
          read_only_enforced: true,
        },
      ),
    );

    const evidence = screen.getByRole("region", { name: "Evidence" });
    const results = screen.getByRole("region", { name: "Query results" });
    expect(evidence).toBeInTheDocument();
    expect(evidence.compareDocumentPosition(results) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("puts the truncation warning above the table it qualifies", () => {
    renderPanel(
      makeResponse({ data: makeData(ROWS, { truncated: true, row_limit: 2 }) }),
    );

    const banner = screen.getByTestId("truncation-banner");
    const table = screen.getByRole("table");
    expect(banner).toHaveTextContent("Showing a partial result.");
    expect(banner.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders no truncation warning for a complete result", () => {
    renderPanel(makeResponse({ data: makeData(ROWS, { truncated: false }) }));
    expect(screen.queryByTestId("truncation-banner")).not.toBeInTheDocument();
  });

  it("labels the SQL with the policy decision and its ruleset version", () => {
    renderPanel(
      makeResponse(
        { sql: "SELECT city FROM customers", data: makeData(ROWS) },
        { policy_decision: "allow", policy_version: "sql_policy@2.0.0" },
      ),
    );
    expect(screen.getByText("policy: allow · sql_policy@2.0.0")).toBeInTheDocument();
  });

  it("falls back to the v1 validation wording when no policy decision is returned", () => {
    renderPanel(makeResponse({ validation_passed: true, data: makeData(ROWS) }));
    expect(screen.getByText("validation passed")).toBeInTheDocument();
  });

  it("lists the tables the executor actually resolved, preferring metadata", () => {
    renderPanel(
      makeResponse(
        { selected_tables: ["stale_guess"], data: makeData(ROWS) },
        { tables_used: ["customers", "orders"] },
      ),
    );

    const heading = screen.getByText("Tables used");
    const group = heading.parentElement as HTMLElement;
    expect(within(group).getByText("customers")).toBeInTheDocument();
    expect(within(group).getByText("orders")).toBeInTheDocument();
    expect(within(group).queryByText("stale_guess")).not.toBeInTheDocument();
  });

  it("offers follow-up questions and passes the chosen one back", async () => {
    const user = userEvent.setup();
    const onFollowUp = vi.fn();
    render(
      <WorkspaceProvider>
        <ResultsPanel
          response={makeResponse({
            data: makeData(ROWS),
            follow_up_questions: ["Which city grew fastest?"],
          })}
          onFollowUp={onFollowUp}
        />
      </WorkspaceProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Which city grew fastest?" }));
    expect(onFollowUp).toHaveBeenCalledWith("Which city grew fastest?");
  });

  it("hands the edited statement back to the caller so it re-enters the same policy path", async () => {
    const user = userEvent.setup();
    const onRerun = vi.fn();
    renderPanel(makeResponse({ sql: "SELECT 1", data: makeData(ROWS) }), onRerun);

    await user.click(screen.getByRole("button", { name: /Edit & run/i }));
    const editor = screen.getByLabelText("Edit SQL");
    expect(editor).toHaveValue("SELECT 1");
    expect(screen.getByText(/read-only policy engine/i)).toBeInTheDocument();

    await user.clear(editor);
    await user.type(editor, "SELECT 2");
    await user.click(screen.getByRole("button", { name: /Run this SQL/i }));

    await waitFor(() => expect(onRerun).toHaveBeenCalledWith("SELECT 2"));
  });

  it("renders nothing SQL-related when the response carried no statement", () => {
    renderPanel(makeResponse({ sql: null, data: null }));
    expect(screen.queryByText("Generated SQL")).not.toBeInTheDocument();
  });
});
