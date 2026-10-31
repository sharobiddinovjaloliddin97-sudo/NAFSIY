import { CsrfError, csrfHeaders, csrfRejected } from "@/lib/csrf";

export const API = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export class UserDataError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function accountRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase())) {
    try {
      for (const [name, value] of Object.entries(await csrfHeaders(false, options.signal ?? undefined))) headers.set(name, value);
    } catch (error) {
      if (error instanceof CsrfError) throw new UserDataError(error.status, error.message);
      throw error;
    }
  }
  const response = await fetch(`${API}/api/v1${path}`, {
    ...options, credentials: "include", cache: "no-store", headers,
    signal: options.signal
      ? AbortSignal.any([options.signal, AbortSignal.timeout(20000)])
      : AbortSignal.timeout(20000),
  });
  if (response.status === 204) return null as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/me") {
      window.dispatchEvent(new Event("nafsiy-auth-changed"));
    }
    const detail = data && typeof data === "object" && "detail" in data &&
      typeof data.detail === "string" ? data.detail : "Ma’lumotni saqlab bo‘lmadi";
    if (csrfRejected(response.status, detail)) {
      throw new UserDataError(403, "Xavfsizlik tokeni yangilanishi kerak. So‘rovni qayta yuboring");
    }
    throw new UserDataError(response.status, response.status >= 500
      ? "Bulut xizmati vaqtincha mavjud emas. Qayta urinib ko‘ring" : detail);
  }
  return data as T;
}

export async function currentAccount(signal?: AbortSignal): Promise<{ id: string } | null> {
  try {
    const user = await accountRequest<{ id: string }>("/auth/me", { signal });
    if (!user || typeof user.id !== "string") throw new Error("Akkaunt javobi noto‘g‘ri");
    return user;
  } catch (error) {
    if (error instanceof UserDataError && error.status === 401) return null;
    throw error;
  }
}

export function userRequest<T>(path: string, options: RequestInit = {}) {
  return accountRequest<T>(`/user${path}`, options);
}
