import { NextRequest, NextResponse } from "next/server";
import { decideRouteGuard } from "@/lib/route-guard";

const SESSION_COOKIE_NAME = "truffler_session";

/**
 * Fast, cookie-presence-only redirect for UX. This is not the security
 * boundary — every protected page and API route independently verifies the
 * session against the backend. This proxy just avoids flashing protected UI
 * before that check can run.
 */
export default function proxy(request: NextRequest) {
  const decision = decideRouteGuard(
    request.nextUrl.pathname,
    request.cookies.has(SESSION_COOKIE_NAME),
  );

  if (decision.action === "redirect" && decision.to === "/login") {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("from", decision.from);
    return NextResponse.redirect(loginUrl);
  }

  if (decision.action === "redirect" && decision.to === "/") {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next|api|health|ready|favicon.ico).*)"],
};
