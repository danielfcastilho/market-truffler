import { cookies } from "next/headers";

const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Server-side fetch to the FastAPI backend, forwarding the visitor's session
 * cookie. Used from Server Components/layouts so protected routes are
 * enforced with a real backend check before any HTML is rendered.
 */
export async function serverFetch(path: string, init?: RequestInit): Promise<Response> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore.toString();

  return fetch(`${API_INTERNAL_URL}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      cookie: cookieHeader,
    },
    cache: "no-store",
  });
}

export interface CurrentUser {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export async function getCurrentUser(): Promise<CurrentUser | null> {
  try {
    const response = await serverFetch("/api/me");
    if (!response.ok) return null;
    return (await response.json()) as CurrentUser;
  } catch {
    return null;
  }
}

export interface SystemInfo {
  environment: string;
  version: string;
  database: "ok" | "unreachable";
  authenticated_user: string | null;
}

export async function getSystemInfo(): Promise<SystemInfo | null> {
  try {
    const response = await serverFetch("/api/system");
    if (!response.ok) return null;
    return (await response.json()) as SystemInfo;
  } catch {
    return null;
  }
}
