import { NextRequest, NextResponse } from "next/server";

/**
 * Proxies /api/* from the browser to the FastAPI backend, forwarding
 * cookies both ways. Implemented as a runtime route handler (rather than a
 * static `next.config.ts` rewrite) so the backend URL is read from
 * `API_INTERNAL_URL` at request time — a `next.config.ts` rewrite bakes its
 * destination host into the build, which breaks when the same image is
 * deployed against a different internal hostname (e.g. Docker's `api`
 * service vs. `localhost` in local dev).
 */
const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

async function proxy(request: NextRequest, path: string[]): Promise<NextResponse> {
  const search = request.nextUrl.search;
  const targetUrl = `${API_INTERNAL_URL}/api/${path.join("/")}${search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");

  const hasBody = !["GET", "HEAD"].includes(request.method);

  let response: Response;
  try {
    response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
    });
  } catch {
    return NextResponse.json({ detail: "API is unreachable" }, { status: 503 });
  }

  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("transfer-encoding");

  const nextResponse = new NextResponse(response.body, {
    status: response.status,
    headers: responseHeaders,
  });

  for (const cookie of response.headers.getSetCookie()) {
    nextResponse.headers.append("set-cookie", cookie);
  }

  return nextResponse;
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, (await context.params).path);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  return proxy(request, (await context.params).path);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxy(request, (await context.params).path);
}
