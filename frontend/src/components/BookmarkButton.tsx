"use client";

import { useSyncExternalStore } from "react";
import { Bookmark, BookmarkCheck, Loader2 } from "lucide-react";
import { getBookmarks, getServerBookmarks, subscribeBookmarks, toggleBookmark } from "@/lib/bookmarks";

type Props = { kind: "quran" | "hadith"; reference: string; title: string };

export default function BookmarkButton({ kind, reference, title }: Props) {
  const state = useSyncExternalStore(subscribeBookmarks, getBookmarks, getServerBookmarks);
  const saved = state.items.some(item => item.kind === kind && item.reference === reference);
  const loading = state.checking || state.pending.includes(`${kind}:${reference}`);
  return (
    <button
      type="button"
      className="bookmark-button"
      onClick={async () => {
        try {
          await toggleBookmark({ kind, reference, title: title.slice(0, 200) });
        } catch (error) {
          window.alert(error instanceof Error ? error.message : "Bookmarkni saqlab bo‘lmadi");
        }
      }}
      disabled={loading}
      title={state.error || (saved ? "Bookmarkni olib tashlash" : "Saqlash")}
      aria-label={saved ? "Saqlanganni o‘chirish" : "Saqlash"}
      aria-pressed={saved}
    >
      {loading ? <Loader2 size={17} className="spin" />
        : saved ? <BookmarkCheck size={17} /> : <Bookmark size={17} />}
    </button>
  );
}
