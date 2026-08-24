/**
 * End-to-end coverage of the console against a fully stubbed API.
 *
 * These specs exist to catch the class of bug unit tests structurally cannot: the wiring between
 * the workspace provider, the network layer and the rendered evidence. Every `/api/*` route is
 * intercepted, so the suite needs neither a FastAPI process nor a database — see
 * `playwright.config.ts` for why that matters.
 *
 * The stubbed payloads are deliberately shaped like the real contract, including the v2 metadata
 * fields (`policy_version`, `policy_decision`, `tables_used`, `read_only_enforced`, `truncated`,
 * `error_category`), so a change to that contract breaks a test rather than silently degrading the
 * UI to "not reported" chips.
 */
import { expect, test, type Page } from "@playwright/test";

const DATABASES = {
  databases: [
    {
      db_flag: "demo",
      db_type: "postgresql",
      description: "Built-in sample store",
      is_public: true,
    },
  ],
};

const ROWS = [
  { city: "Mumbai", customers: 12 },
  { city: "Pune", customers: 9 },
  { city: "Delhi", customers: 4 },
];

const SQL = "SELECT city, COUNT(*) AS customers FROM customers GROUP BY city";

function successPayload(overrides: Record<string, unknown> = {}) {
  return {
    status: "success",
    sql: SQL,
    validation_passed: true,
    data: {
      results: ROWS,
      sql: SQL,
      row_count: ROWS.length,
      execution_time_ms: 31.4,
      csv: "city,customers\r\nMumbai,12\r\nPune,9\r\nDelhi,4",
      raw_json: JSON.stringify(ROWS),
      describe_text: "",
      truncated: false,
      row_limit: 1000,
      page: null,
      page_size: null,
      has_next: null,
      total_rows: 3,
    },
    error: null,
    selected_tables: ["customers"],
    follow_up_questions: ["Which city grew fastest?"],
    natural_summary: "Mumbai has the most customers, with 12.",
    metadata: {
      execution_time_ms: 31.4,
      total_rows: 3,
      policy_version: "sql_policy@2.0.0",
      policy_decision: "allow",
      sql_fingerprint: "3f9a1c72b8e4d0a5",
      tables_used: ["customers"],
      truncated: false,
      read_only_enforced: true,
      error_category: null,
    },
    ...overrides,
  };
}

/** Stubs everything the console fetches, so nothing leaves the browser. */
async function stubApi(page: Page, queryPayload: unknown): Promise<void> {
  await page.route("**/api/health", (route) =>
    route.fulfill({
      json: { status: "healthy", message: "DBWhisper API is running", version: "0.1.0" },
    }),
  );
  await page.route("**/api/databases", (route) => route.fulfill({ json: DATABASES }));
  await page.route("**/api/training/pairs**", (route) => route.fulfill({ json: { pairs: [] } }));
  await page.route("**/api/query", (route) => route.fulfill({ json: queryPayload }));
}

/**
 * Waits for React to take over the server-rendered markup.
 *
 * This matters more than it looks: the query box is present in the SSR HTML before hydration, so a
 * `fill` + `click` that lands too early submits the form natively, navigates, and silently discards
 * the typed question. The database picker is populated by a client effect from the stubbed
 * `/api/databases` response, so a value there is proof that both hydration and effects have run.
 *
 * It is addressed by label rather than by role because the control changes role once the list
 * arrives: a free-text `input` when no databases are known, a `select` once they are.
 */
async function waitForHydration(page: Page): Promise<void> {
  await expect(page.getByLabel("Database")).toHaveValue("demo", { timeout: 30_000 });
}

async function ask(page: Page, question: string): Promise<void> {
  await waitForHydration(page);
  const box = page.locator("#query");
  await expect(box).toBeVisible();
  await box.fill(question);
  await page.getByRole("button", { name: "Run query" }).click();
}

test.describe("console — successful query", () => {
  test("shows the summary, the SQL, the evidence strip and the rows", async ({ page }) => {
    await stubApi(page, successPayload());
    await page.goto("/app");
    await ask(page, "How many customers per city?");

    await expect(page.getByText("Mumbai has the most customers, with 12.")).toBeVisible();

    // The generated statement is shown with the answer, not instead of it. (The highlighter splits
    // it across spans, so assert on the <code> element's text rather than on a text node.)
    await expect(page.locator("pre code").first()).toContainText(SQL);
    await expect(page.getByText("policy: allow · sql_policy@2.0.0")).toBeVisible();

    // Evidence strip: the mechanisms the marketing page promises, rendered from real metadata.
    const evidence = page.getByRole("region", { name: "Evidence" });
    await expect(evidence).toBeVisible();
    await expect(evidence.locator('[data-evidence="grounding"]')).toContainText(
      "Schema grounded · 1 table",
    );
    await expect(evidence.locator('[data-evidence="policy"]')).toHaveAttribute("data-state", "ok");
    await expect(evidence.locator('[data-evidence="read-only"]')).toContainText(
      "Read-only session",
    );
    await expect(evidence.locator('[data-evidence="truncated"]')).toContainText("Complete result");
    await expect(evidence.locator('[data-evidence="fingerprint"]')).toContainText("3f9a1c72b8e4");

    // No truncation warning on a complete result.
    await expect(page.getByTestId("truncation-banner")).toHaveCount(0);

    // The data itself.
    const table = page.getByRole("table");
    await expect(table.getByRole("row")).toHaveCount(ROWS.length + 1);
    await expect(table.getByRole("cell", { name: "Mumbai" })).toBeVisible();

    // Follow-ups are offered.
    await expect(page.getByRole("button", { name: "Which city grew fastest?" })).toBeVisible();
  });

  test("sorting a column reorders the rendered rows", async ({ page }) => {
    await stubApi(page, successPayload());
    await page.goto("/app");
    await ask(page, "How many customers per city?");

    const firstCell = page.getByRole("table").getByRole("row").nth(1).getByRole("cell").first();
    await expect(firstCell).toHaveText("Mumbai");

    await page.getByRole("button", { name: "Sort by city" }).click();
    await expect(firstCell).toHaveText("Delhi");
  });
});

test.describe("console — truncated result", () => {
  test("warns above the table that the answer is partial", async ({ page }) => {
    const payload = successPayload();
    (payload.data as Record<string, unknown>).truncated = true;
    (payload.data as Record<string, unknown>).row_limit = 3;
    (payload.metadata as Record<string, unknown>).truncated = true;
    await stubApi(page, payload);

    await page.goto("/app");
    await ask(page, "List every order");

    const banner = page.getByTestId("truncation-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Showing a partial result.");
    await expect(banner).toContainText("row cap of 3");

    // The warning precedes the table it qualifies.
    const bannerBox = await banner.boundingBox();
    const tableBox = await page.getByRole("table").boundingBox();
    expect(bannerBox).not.toBeNull();
    expect(tableBox).not.toBeNull();
    expect(bannerBox!.y).toBeLessThan(tableBox!.y);

    await expect(
      page.getByRole("region", { name: "Evidence" }).locator('[data-evidence="truncated"]'),
    ).toHaveAttribute("data-state", "warn");
  });
});

test.describe("console — refused query", () => {
  test("surfaces the refusal and which layer produced it", async ({ page }) => {
    await stubApi(page, {
      status: "error",
      sql: "DELETE FROM customers",
      validation_passed: false,
      data: null,
      error: "Only SELECT statements are permitted (got Delete)",
      selected_tables: null,
      follow_up_questions: null,
      natural_summary: null,
      metadata: {
        execution_time_ms: null,
        total_rows: null,
        policy_version: "sql_policy@2.0.0",
        policy_decision: "deny",
        tables_used: ["customers"],
        read_only_enforced: null,
        error_category: "policy",
      },
    });

    await page.goto("/app");
    await ask(page, "Delete every customer");

    // Next injects an empty route-announcer with role="alert", so narrow to the one with content.
    const alert = page.getByRole("alert").filter({ hasText: "Something went wrong" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("Only SELECT statements are permitted");

    const evidence = page.getByRole("region", { name: "Evidence" });
    await expect(evidence.locator('[data-evidence="policy"]')).toContainText("Policy: deny");
    await expect(evidence.locator('[data-evidence="policy"]')).toHaveAttribute(
      "data-state",
      "risk",
    );
    await expect(evidence.locator('[data-evidence="error-category"]')).toContainText(
      "Failure: policy",
    );
    // Unreported mechanisms stay visibly unknown rather than turning into reassurance.
    await expect(evidence.locator('[data-evidence="read-only"]')).toHaveAttribute(
      "data-state",
      "unknown",
    );

    await expect(page.getByRole("table")).toHaveCount(0);
  });
});

test.describe("console — accessibility affordances", () => {
  test("announces completion and moves focus to the results region", async ({ page }) => {
    await stubApi(page, successPayload());
    await page.goto("/app");
    await ask(page, "How many customers per city?");

    // `exact` matters: "Results" would otherwise also match the inner "Query results" region.
    const results = page.getByRole("region", { name: "Results", exact: true });
    await expect(results).toBeFocused();
    await expect(results.getByRole("status").first()).toContainText("Query complete: 3 rows");
  });

  test("runs a query from the keyboard with Ctrl+Enter", async ({ page }) => {
    await stubApi(page, successPayload());
    await page.goto("/app");

    await waitForHydration(page);
    const box = page.locator("#query");
    await box.click();
    await box.fill("How many customers per city?");
    await box.press("Control+Enter");

    await expect(page.getByText("Mumbai has the most customers, with 12.")).toBeVisible();
  });
});
