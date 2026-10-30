const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Tokens = { csrf_token: string | null; auth_csrf_token: string; expires_at: number; session_scope: string | null };
let cached: Tokens | null = null;
let pending: Promise<Tokens> | null = null;
let epoch = 0;
let listening = false;
let knownScope: string | null | undefined;

export class CsrfError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export function clearCsrf() {
  knownScope = undefined;
  invalidateTokens();
}

function invalidateTokens() {
  epoch++;
  cached = null;
  pending = null;
}

async function tokens(): Promise<Tokens> {
  if (!listening && typeof window !== "undefined") {
    window.addEventListener("nafsiy-auth-changed", clearCsrf);
    listening = true;
  }
  if (cached && cached.expires_at * 1000 > Date.now() + 15000) return cached;
  if (pending) return pending;
  const version = epoch;
  const operation = (async () => {
    const response = await fetch(`${API}/api/v1/auth/csrf`, {
      credentials: "include", cache: "no-store", headers: { "X-CSRF-Request": "1" },
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new CsrfError(response.status, response.status === 429
      ? "Juda ko‘p urinish. Birozdan keyin qayta urinib ko‘ring"
      : "Xavfsizlik tekshiruvini bajarib bo‘lmadi. Qayta urinib ko‘ring");
    const data: unknown = await response.json();
    if (!data || typeof data !== "object" || !("csrf_token" in data) ||
      !(data.csrf_token === null || typeof data.csrf_token === "string") ||
      !("auth_csrf_token" in data) || typeof data.auth_csrf_token !== "string" ||
      !("session_scope" in data) || !(data.session_scope === null || typeof data.session_scope === "string") ||
      !("expires_at" in data) || typeof data.expires_at !== "number") {
      throw new CsrfError(503, "Xavfsizlik javobi noto‘g‘ri");
    }
    if (version !== epoch) throw new CsrfError(409, "Akkaunt o‘zgardi. So‘rovni qayta yuboring");
    if (knownScope !== undefined && knownScope !== data.session_scope) {
      clearCsrf();
      window.dispatchEvent(new Event("nafsiy-auth-changed"));
      throw new CsrfError(409, "Akkaunt sessiyasi o‘zgardi. So‘rovni qayta yuboring");
    }
    knownScope = data.session_scope;
    cached = data as Tokens;
    return cached;
  })();
  pending = operation;
  try { return await operation; }
  finally { if (pending === operation) pending = null; }
}

export async function csrfHeaders(anonymous = false, signal?: AbortSignal): Promise<Record<string, string>> {
  signal?.throwIfAborted();
  const data = await tokens();
  signal?.throwIfAborted();
  const value = anonymous ? data.auth_csrf_token : data.csrf_token;
  if (!value) {
    clearCsrf();
    window.dispatchEvent(new Event("nafsiy-auth-changed"));
    throw new CsrfError(401, "Sessiya tugagan. Akkauntga qayta kiring");
  }
  return { "X-CSRF-Token": value };
}

export async function sessionScope(fresh = false): Promise<string> {
  if (fresh) invalidateTokens();
  const data = await tokens();
  if (!data.session_scope || !data.csrf_token) {
    window.dispatchEvent(new Event("nafsiy-auth-changed"));
    throw new CsrfError(401, "Sessiya tugagan. Qayta kiring");
  }
  return data.session_scope;
}

export function csrfRejected(status: number, detail: unknown): boolean {
  if (status !== 403 || detail !== "CSRF validation failed") return false;
  invalidateTokens();
  return true;
}
