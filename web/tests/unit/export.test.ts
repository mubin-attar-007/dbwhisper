/**
 * Export is the one place where a formatting bug silently corrupts data a user then takes
 * elsewhere, so these tests concentrate on escaping: a comma, a quote or a newline inside a cell
 * must not be able to invent a column, and a pipe must not be able to break a Markdown table.
 *
 * The `exportCsv`/`exportJson` tests assert on the Blob that is handed to `URL.createObjectURL`
 * rather than on "a click happened" — the payload is the behaviour that matters.
 */
import { describe, expect, it, vi } from "vitest";
import { exportCsv, exportJson, rowsToCsv, rowsToMarkdown, toMarkdown } from "@/src/lib/export";
import { deriveColumns } from "@/src/lib/rows";
import { makeData } from "./fixtures";

/**
 * jsdom's `Blob` predates `Blob.prototype.text()`, so fall back to `FileReader` there. The
 * production code never reads a Blob back; this is purely a test-side affordance.
 */
function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === "function") return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

/** Captures the text of the Blob passed to createObjectURL during `run`. */
async function captureDownload(run: () => void): Promise<string> {
  let captured: Blob | null = null;
  const createSpy = vi
    .spyOn(URL, "createObjectURL")
    .mockImplementation((blob: Blob | MediaSource) => {
      captured = blob as Blob;
      return "blob:captured";
    });
  const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);

  try {
    run();
    expect(createSpy).toHaveBeenCalledTimes(1);
    return captured ? await blobText(captured as Blob) : "";
  } finally {
    createSpy.mockRestore();
    revokeSpy.mockRestore();
    clickSpy.mockRestore();
  }
}

describe("rowsToCsv", () => {
  it("writes a header row and CRLF line endings", () => {
    const rows = [{ a: 1, b: 2 }];
    expect(rowsToCsv(rows, ["a", "b"])).toBe("a,b\r\n1,2");
  });

  it("quotes and doubles embedded quotes", () => {
    const rows = [{ note: 'he said "hi"' }];
    expect(rowsToCsv(rows, ["note"])).toBe('note\r\n"he said ""hi"""');
  });

  it("quotes values containing a comma or a newline", () => {
    const rows = [{ a: "x,y", b: "line1\nline2" }];
    expect(rowsToCsv(rows, ["a", "b"])).toBe('a,b\r\n"x,y","line1\nline2"');
  });

  it("emits an empty field for a missing key rather than the string 'undefined'", () => {
    expect(rowsToCsv([{ a: 1 }], ["a", "b"])).toBe("a,b\r\n1,");
  });
});

describe("rowsToMarkdown", () => {
  it("builds a header, separator and body", () => {
    expect(rowsToMarkdown([{ a: 1, b: 2 }], ["a", "b"])).toBe(
      "| a | b |\n| --- | --- |\n| 1 | 2 |",
    );
  });

  it("escapes pipes so a cell cannot break out of its column", () => {
    expect(rowsToMarkdown([{ a: "x|y" }], ["a"])).toContain("| x\\|y |");
  });

  it("still emits a valid table when there are no rows", () => {
    expect(rowsToMarkdown([], ["a"])).toBe("| a |\n| --- |");
  });
});

describe("toMarkdown", () => {
  it("derives its columns from the resolved rows", () => {
    const data = makeData([{ city: "Mumbai", n: 3 }]);
    expect(toMarkdown(data)).toBe("| city | n |\n| --- | --- |\n| Mumbai | 3 |");
  });
});

describe("exportCsv", () => {
  it("prefers the server-rendered csv payload", async () => {
    const data = makeData([{ a: 1 }], { csv: "server,payload\r\n1,2" });
    await expect(captureDownload(() => exportCsv(data))).resolves.toBe("server,payload\r\n1,2");
  });

  it("derives the csv when the server payload is blank", async () => {
    const data = makeData([{ a: 1, b: "x,y" }], { csv: "   " });
    await expect(captureDownload(() => exportCsv(data))).resolves.toBe('a,b\r\n1,"x,y"');
  });
});

describe("exportJson", () => {
  it("prefers the server-rendered raw_json payload verbatim", async () => {
    const data = makeData([{ a: 1 }], { raw_json: '[{"a":1}]' });
    await expect(captureDownload(() => exportJson(data))).resolves.toBe('[{"a":1}]');
  });

  it("serializes the resolved rows when raw_json is blank", async () => {
    const rows = [{ a: 1 }];
    const data = makeData(rows, { raw_json: "" });
    const text = await captureDownload(() => exportJson(data));
    expect(JSON.parse(text)).toEqual(rows);
  });
});

describe("column derivation used by the exporters", () => {
  it("keeps ragged rows aligned to a single header", () => {
    const rows = [{ a: 1 }, { b: 2 }];
    const columns = deriveColumns(rows);
    expect(rowsToCsv(rows, columns)).toBe("a,b\r\n1,\r\n,2");
  });
});
