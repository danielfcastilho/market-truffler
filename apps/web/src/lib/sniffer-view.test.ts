import { describe, expect, it } from "vitest";
import { resolveSnifferView } from "./sniffer-view";

describe("resolveSnifferView", () => {
  it("defaults to truffles when no view is given", () => {
    expect(resolveSnifferView(undefined)).toBe("truffles");
  });

  it("resolves an explicit truffles view", () => {
    expect(resolveSnifferView("truffles")).toBe("truffles");
  });

  it("resolves features", () => {
    expect(resolveSnifferView("features")).toBe("features");
  });

  it("resolves notes", () => {
    expect(resolveSnifferView("notes")).toBe("notes");
  });

  it("falls back to truffles for unrecognized values", () => {
    expect(resolveSnifferView("bogus")).toBe("truffles");
  });

  it("uses the first value when given an array", () => {
    expect(resolveSnifferView(["features", "truffles"])).toBe("features");
  });
});
