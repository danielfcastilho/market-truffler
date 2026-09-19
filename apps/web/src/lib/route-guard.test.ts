import { describe, expect, it } from "vitest";
import { decideRouteGuard } from "./route-guard";

describe("decideRouteGuard", () => {
  it("redirects to /login when there is no session cookie", () => {
    expect(decideRouteGuard("/", false)).toEqual({
      action: "redirect",
      to: "/login",
      from: "/",
    });
  });

  it("preserves the original path so login can redirect back", () => {
    expect(decideRouteGuard("/system", false)).toEqual({
      action: "redirect",
      to: "/login",
      from: "/system",
    });
  });

  it("redirects an already-authenticated visitor away from /login", () => {
    expect(decideRouteGuard("/login", true)).toEqual({ action: "redirect", to: "/" });
  });

  it("lets an unauthenticated visitor stay on /login", () => {
    expect(decideRouteGuard("/login", false)).toEqual({ action: "continue" });
  });

  it("lets an authenticated visitor continue to a protected page", () => {
    expect(decideRouteGuard("/system", true)).toEqual({ action: "continue" });
  });
});
