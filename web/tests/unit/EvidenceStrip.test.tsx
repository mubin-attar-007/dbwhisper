/**
 * Component-level checks that the honesty in `evidence.ts` actually reaches the DOM: a "not
 * reported" mechanism must render as a distinguishable `unknown` chip, and every chip's detail
 * must be readable by a screen reader rather than living only in a `title` attribute.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceStrip } from "@/app/components/EvidenceStrip";
import { TruncationBanner } from "@/app/components/TruncationBanner";
import { makeData, makeResponse } from "./fixtures";

function chip(id: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-evidence="${id}"]`);
  if (!el) throw new Error(`no evidence chip with id "${id}"`);
  return el;
}

describe("EvidenceStrip", () => {
  it("renders one labelled list item per reported mechanism", () => {
    render(
      <EvidenceStrip
        response={makeResponse(
          { data: makeData([], { truncated: false }) },
          {
            tables_used: ["orders", "products"],
            policy_decision: "allow",
            policy_version: "sql_policy@2.0.0",
            read_only_enforced: true,
          },
        )}
      />,
    );

    expect(screen.getByRole("region", { name: "Evidence" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(chip("policy")).toHaveTextContent("Policy: allow");
    expect(chip("policy")).toHaveAttribute("data-state", "ok");
    expect(chip("grounding")).toHaveTextContent("Schema grounded · 2 tables");
    expect(chip("read-only")).toHaveTextContent("Read-only session");
    expect(chip("truncated")).toHaveTextContent("Complete result");
  });

  it("marks unreported mechanisms as unknown rather than as passing", () => {
    render(<EvidenceStrip response={makeResponse()} />);

    for (const id of ["grounding", "policy", "read-only"]) {
      expect(chip(id)).toHaveAttribute("data-state", "unknown");
      expect(chip(id)).toHaveTextContent(/not reported/i);
    }
  });

  it("shows a denied statement as a refusal and surfaces the failure class", () => {
    render(
      <EvidenceStrip
        response={makeResponse(
          { status: "error", validation_passed: false },
          { policy_decision: "deny", error_category: "policy" },
        )}
      />,
    );

    expect(chip("policy")).toHaveAttribute("data-state", "risk");
    expect(chip("error-category")).toHaveTextContent("Failure: policy");
  });

  it("exposes each chip's explanation to assistive technology, not just as a tooltip", () => {
    render(
      <EvidenceStrip
        response={makeResponse({}, { policy_decision: "allow", policy_version: "sql_policy@2.0.0" })}
      />,
    );

    const policy = chip("policy");
    expect(policy).toHaveAttribute("title", expect.stringContaining("sql_policy@2.0.0"));
    expect(policy).toHaveTextContent("Admitted as a single read-only statement");
  });

  it("warns when the result was truncated", () => {
    render(
      <EvidenceStrip
        response={makeResponse({ data: makeData([], { truncated: true, row_limit: 500 }) })}
      />,
    );
    expect(chip("truncated")).toHaveAttribute("data-state", "warn");
    expect(chip("truncated")).toHaveTextContent("Result truncated");
  });
});

describe("TruncationBanner", () => {
  it("renders nothing when truncation was not reported", () => {
    const { container } = render(<TruncationBanner response={makeResponse()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when truncation was reported as false", () => {
    const { container } = render(
      <TruncationBanner response={makeResponse({ data: makeData([], { truncated: false }) })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("announces a partial result politely and names the row cap", () => {
    render(
      <TruncationBanner
        response={makeResponse({ data: makeData([], { truncated: true, row_limit: 1000 }) })}
      />,
    );

    const banner = screen.getByRole("status");
    expect(banner).toHaveAttribute("aria-live", "polite");
    expect(banner).toHaveTextContent("Showing a partial result.");
    expect(banner).toHaveTextContent("row cap of 1,000");
    expect(banner).toHaveTextContent(/describe only the rows returned/i);
  });

  it("still warns when the row cap itself is not reported", () => {
    render(
      <TruncationBanner response={makeResponse({ data: makeData([], { truncated: true }) })} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("The row cap was reached");
  });
});
