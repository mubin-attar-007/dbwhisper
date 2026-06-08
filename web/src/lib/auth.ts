/**
 * Auth API client (session-cookie based). Calls the same-origin /api/auth/* endpoints.
 * `credentials: "include"` ensures the session cookie is sent and stored.
 */
import { API_BASE_URL, ApiError } from "./api";

export interface AuthUser {
  id: number;
  email: string;
  is_admin: boolean;
}

async function parse(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errMsg(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object") {
    const o = payload as Record<string, unknown>;
    if (typeof o.detail === "string" && o.detail) return o.detail;
    if (typeof o.message === "string" && o.message) return o.message;
  }
  return fallback;
}

async function post(path: string, body: object): Promise<unknown> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
    cache: "no-store",
  });
  const payload = await parse(res);
  if (!res.ok) {
    throw new ApiError(errMsg(payload, `Request failed (HTTP ${res.status})`), res.status, payload);
  }
  return payload;
}

/** GET /auth/me — returns the current user, or null when not signed in (401). */
export async function getMe(signal?: AbortSignal): Promise<AuthUser | null> {
  const res = await fetch(`${API_BASE_URL}/auth/me`, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (res.status === 401) return null;
  const payload = await parse(res);
  if (!res.ok) {
    throw new ApiError(
      errMsg(payload, `Failed to load session (HTTP ${res.status})`),
      res.status,
      payload,
    );
  }
  return payload as AuthUser;
}

export async function login(email: string, password: string): Promise<AuthUser> {
  return (await post("/auth/login", { email, password })) as AuthUser;
}

export async function register(email: string, password: string): Promise<AuthUser> {
  return (await post("/auth/register", { email, password })) as AuthUser;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
  });
}
