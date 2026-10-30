export const PENDING_KEY = "nafsiy.pending-ai.v1";
const MAX_AGE = 7 * 24 * 60 * 60 * 1000;
export type PendingAI = { key: string; sessionId: string; scope: string; digest: string; createdAt: number };

export function pendingCreatedAt(saved: PendingAI | null, key: string) {
  return saved?.key === key ? saved.createdAt : Date.now();
}

export async function questionDigest(question: string) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(question));
  return Array.from(new Uint8Array(hash), item => item.toString(16).padStart(2, "0")).join("");
}

export function forgetPending() {
  try { localStorage.removeItem(PENDING_KEY); } catch { return; }
}

export function readPending(): PendingAI | null {
  try {
    const value = JSON.parse(localStorage.getItem(PENDING_KEY) || "null");
    if (!value) return null;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.key) || !/^[0-9a-f]{32}$/.test(value.sessionId) ||
      !/^[0-9a-f]{64}$/.test(value.scope) || !/^[0-9a-f]{64}$/.test(value.digest) ||
      !Number.isFinite(value.createdAt) || value.createdAt > Date.now() || Date.now() - value.createdAt > MAX_AGE) {
      forgetPending();
      return null;
    }
    return { key: value.key, sessionId: value.sessionId, scope: value.scope, digest: value.digest, createdAt: value.createdAt };
  } catch { forgetPending(); return null; }
}

export function rememberPending(value: PendingAI): boolean {
  try { localStorage.setItem(PENDING_KEY, JSON.stringify(value)); return true; }
  catch { return false; }
}
