/**
 * Global test setup.
 *
 * Adds jest-dom's DOM matchers (and their TypeScript augmentation, which is why the import is a
 * side-effect import rather than a named one) and unmounts React trees between tests so a leaked
 * component from one test cannot answer a query in the next.
 *
 * jsdom does not implement `URL.createObjectURL`/`revokeObjectURL`, which the export helpers call.
 * They are stubbed here as no-ops so a component that renders an export control does not explode;
 * tests that care about the download itself replace these with spies.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:stub";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => undefined;
}

afterEach(() => {
  cleanup();
});
