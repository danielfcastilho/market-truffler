export type RouteGuardDecision =
  | { action: "redirect"; to: "/login"; from: string }
  | { action: "redirect"; to: "/" }
  | { action: "continue" };

/**
 * Pure decision logic for the cookie-presence-only redirect in `proxy.ts`.
 * Kept separate from the Next.js request/response types so it can be unit
 * tested without constructing a NextRequest.
 */
export function decideRouteGuard(pathname: string, hasSessionCookie: boolean): RouteGuardDecision {
  const isLoginPage = pathname === "/login";

  if (!hasSessionCookie && !isLoginPage) {
    return { action: "redirect", to: "/login", from: pathname };
  }

  if (hasSessionCookie && isLoginPage) {
    return { action: "redirect", to: "/" };
  }

  return { action: "continue" };
}
