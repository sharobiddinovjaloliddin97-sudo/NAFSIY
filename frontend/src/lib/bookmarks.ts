import { currentAccount, userRequest } from "@/lib/userData";

export type StoredBookmark = {
  id: string;
  kind: "quran" | "hadith";
  reference: string;
  title: string;
};

type Snapshot = {
  items: StoredBookmark[];
  userId: string | null;
  checking: boolean;
  error: string;
  pending: string[];
};

const INITIAL: Snapshot = { items: [], userId: null, checking: true, error: "", pending: [] };
let snapshot = INITIAL;
let generation = 0;
let controller: AbortController | null = null;
const listeners = new Set<() => void>();

function publish(next: Snapshot) {
  snapshot = next;
  listeners.forEach(listener => listener());
}

function readBookmark(value: unknown): StoredBookmark {
  if (!value || typeof value !== "object" || !("id" in value) || typeof value.id !== "string" ||
    !("kind" in value) || (value.kind !== "quran" && value.kind !== "hadith") ||
    !("reference" in value) || typeof value.reference !== "string" ||
    !("title" in value) || typeof value.title !== "string") {
    throw new Error("Saqlanganlar javobi noto‘g‘ri");
  }
  return value as StoredBookmark;
}

async function refresh() {
  const version = ++generation;
  controller?.abort();
  const request = new AbortController();
  controller = request;
  publish(INITIAL);
  try {
    const user = await currentAccount(request.signal);
    if (version !== generation) return;
    if (!user) {
      publish({ ...INITIAL, checking: false });
      return;
    }
    const data = await userRequest<unknown>("/bookmarks", { signal: request.signal });
    if (version !== generation) return;
    if (!Array.isArray(data)) throw new Error("Saqlanganlar javobi noto‘g‘ri");
    publish({ ...INITIAL, items: data.map(readBookmark), userId: user.id, checking: false });
  } catch (error) {
    if (version !== generation || request.signal.aborted) return;
    publish({ ...INITIAL, checking: false, error: error instanceof Error ? error.message : "Saqlanganlar yuklanmadi" });
  }
}

function changed() { void refresh(); }

export function subscribeBookmarks(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    window.addEventListener("nafsiy-auth-changed", changed);
    void refresh();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      window.removeEventListener("nafsiy-auth-changed", changed);
      generation++;
      controller?.abort();
      snapshot = INITIAL;
    }
  };
}

export const getBookmarks = () => snapshot;
export const getServerBookmarks = () => INITIAL;

export async function toggleBookmark(item: Omit<StoredBookmark, "id">) {
  if (snapshot.checking) return;
  if (snapshot.error) {
    void refresh();
    throw new Error("Saqlanganlar qayta yuklanmoqda. Birozdan keyin urinib ko‘ring");
  }
  if (!snapshot.userId) throw new Error("Bookmark saqlash uchun akkauntga kiring.");
  const key = `${item.kind}:${item.reference}`;
  if (snapshot.pending.includes(key)) return;
  const version = generation;
  const saved = snapshot.items.find(entry => entry.kind === item.kind && entry.reference === item.reference);
  publish({ ...snapshot, pending: [...snapshot.pending, key] });
  try {
    const result = await userRequest<unknown>(saved ? `/bookmarks/${encodeURIComponent(saved.id)}` : "/bookmarks", {
      method: saved ? "DELETE" : "POST",
      ...(!saved ? { body: JSON.stringify(item) } : {}),
      signal: controller?.signal,
    });
    if (version !== generation) return;
    publish({ ...snapshot, items: saved ? snapshot.items.filter(entry => entry.id !== saved.id)
      : [...snapshot.items, readBookmark(result)] });
  } catch (error) {
    if (version === generation) throw error;
  } finally {
    if (version === generation) publish({ ...snapshot, pending: snapshot.pending.filter(entry => entry !== key) });
  }
}
