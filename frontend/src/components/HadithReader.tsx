"use client";

import { useEffect, useRef, useState } from "react";
import BookmarkButton from "@/components/BookmarkButton";
import {
  BookMarked,
  Search,
  Copy,
  Check,
  Loader2,
  ArrowLeft,
  BookOpen
} from "lucide-react";

const API =
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

type Collection = {
  key: string;
  name: string;
  arabic_name?: string;
  author?: string;
  total_hadiths?: number;
};

type RecordData = Record<string, unknown>;

type Hadith = {
  collection: string;
  number: string;
  arabic: string;
  english: string;
  narrator: string;
  grade: string;
  book: string;
  chapter: string;
  raw: RecordData;
};

const COLLECTION_NAMES: Record<string, string> = {
  bukhari: "Sahih al-Bukhari",
  muslim: "Sahih Muslim",
  abudawud: "Sunan Abu Dawud",
  tirmidhi: "Jami at-Tirmidhi",
  ibnmajah: "Sunan Ibn Majah",
  nasai: "Sunan an-Nasa'i",
  malik: "Muwatta Malik",
  nawawi: "Nawawi's 40 Hadith",
  qudsi: "40 Hadith Qudsi",
  dehlawi: "Shah Waliullah's 40 Hadith"
};

function obj(value: unknown): RecordData {
  return value && typeof value === "object" &&
    !Array.isArray(value)
    ? value as RecordData
    : {};
}

function str(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return "";
}

function first(...values: unknown[]): string {
  return values.map(str).find(Boolean) || "";
}

function payload(value: unknown): unknown {
  let result = value;
  for (let depth = 0; depth < 3; depth++) {
    const outer = obj(result);
    if (outer.data === undefined) break;
    result = outer.data;
  }
  return result;
}

function normalizeHadith(
  value: unknown,
  fallbackCollection = ""
): Hadith {
  const data = obj(payload(value));
  if (!Object.keys(data).length) throw new Error("Hadis formati noto‘g‘ri");
  const meta = obj(data.metadata);

  const collection = first(
    data.collection,
    data.collection_key,
    data.collectionKey,
    fallbackCollection
  );

  return {
    collection,
    number: first(
      data.hadithnumber,
      data.hadith_number,
      data.hadithNumber,
      data.number,
      data.id
    ),
    arabic: first(
      data.hadith_arabic,
      data.hadithArabic,
      data.arabic,
      data.arab,
      data.arabic_text
    ),
    english: first(
      data.hadith_english,
      data.hadithEnglish,
      data.english,
      data.english_text,
      data.text
    ),
    narrator: first(
      data.narrator,
      data.narrated_by,
      data.narratedBy
    ),
    grade: first(
      data.grade,
      data.grading,
      meta.grade
    ),
    book: first(
      data.book,
      data.book_name,
      data.bookName
    ),
    chapter: first(
      data.chapter,
      data.chapter_name,
      data.chapterName
    ),
    raw: data
  };
}

function extractResults(value: unknown): unknown[] {
  const root = payload(value);

  if (Array.isArray(root)) return root;

  const data = obj(root);

  for (const key of [
    "results",
    "hadiths",
    "items",
    "matches",
    "search_results"
  ]) {
    if (Array.isArray(data[key])) {
      return data[key] as unknown[];
    }
  }

  throw new Error("Qidiruv natijalari formati noto‘g‘ri");
}

async function getData(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(`${API}${path}`, { signal });

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = str(obj(data).detail);
    throw new Error(
      detail || `API xatosi: HTTP ${response.status}`
    );
  }

  if (data === null) throw new Error("API javobi JSON formatida emas");
  return data;
}

function HadithCard({ item, copied, onCopy }: {
  item: Hadith;
  copied: boolean;
  onCopy: (item: Hadith) => Promise<void>;
}) {
  return (
    <article className="hadith-record">
      <div className="hadith-record-top">
        <div>
          <span className="hadith-ref">
            {COLLECTION_NAMES[item.collection] ||
              item.collection ||
              "Hadis"}
            {item.number ? ` · #${item.number}` : ""}
          </span>
        </div>
        <div className="reader-actions">
        {item.collection && /^[1-9]\d*$/.test(item.number) && (
          <BookmarkButton
            kind="hadith"
            reference={`${item.collection}:${item.number}`}
            title={`${item.collection} #${item.number}`}
          />
        )}
        <button
          type="button"
          className="hadith-copy"
          onClick={() => onCopy(item)}
          aria-label="Hadisni nusxalash"
        >
          {copied
            ? <Check size={17} />
            : <Copy size={17} />}
        </button>
        </div>
      </div>

      {item.arabic && (
        <p className="hadith-arabic" dir="rtl">
          {item.arabic}
        </p>
      )}

      {item.english && (
        <div className="hadith-english">
          <span>ENGLISH TRANSLATION</span>
          <p>{item.english}</p>
        </div>
      )}

      {!item.arabic && !item.english && (
        <p className="hadith-empty">
            Ushbu yozuv uchun arabcha yoki inglizcha hadis matni mavjud emas.
        </p>
      )}

      <div className="hadith-meta">
        {item.narrator && (
          <div><strong>Roviy:</strong> {item.narrator}</div>
        )}
        {item.book && (
          <div><strong>Kitob:</strong> {item.book}</div>
        )}
        {item.chapter && (
          <div><strong>Bob:</strong> {item.chapter}</div>
        )}
        {item.grade && (
          <div><strong>API bahosi:</strong> {item.grade}</div>
        )}
      </div>

      <p className="hadith-caution">
        Manba: UmmahAPI. Hadis matni, raqamlash va
        sahihlik bahosi mustaqil tasdiqlanmagan. grading_not_verified.
      </p>
    </article>
  );
}

export default function HadithReader() {
  const [collections, setCollections] =
    useState<Collection[]>([]);

  const [collection, setCollection] = useState("");
  const [number, setNumber] = useState("1");
  const [hadith, setHadith] = useState<Hadith | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Hadith[]>([]);
  const [searchMode, setSearchMode] = useState(false);

  const [loading, setLoading] = useState(false);
  const [collectionsLoading, setCollectionsLoading] =
    useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState<Hadith | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    requestRef.current?.abort();
    if (copyTimer.current !== null) clearTimeout(copyTimer.current);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    getData("/api/v1/hadith/collections", controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;

        const data = obj(payload(result));
        const list = Array.isArray(data.collections)
          ? data.collections
          : Array.isArray(payload(result))
            ? payload(result) as unknown[]
            : null;
        if (!list) throw new Error("To‘plamlar formati noto‘g‘ri");

        const seen = new Set<string>();
        const normalized = list.map(item => {
            const record = obj(item);
            const key = first(record.key, record.id);
            return {
              key,
              name: first(record.name, record.title, COLLECTION_NAMES[key], key),
              arabic_name: str(record.arabic_name),
              author: str(record.author),
              total_hadiths:
                Number(record.total_hadiths) || undefined
            };
          }).filter(item => {
            if (!/^[a-z0-9-]{2,50}$/.test(item.key) || seen.has(item.key)) return false;
            seen.add(item.key);
            return true;
          });
        if (list.length && !normalized.length) throw new Error("To‘plamlar formati noto‘g‘ri");
        setCollections(normalized);
        setCollection(previous => normalized.some(item => item.key === previous)
          ? previous : normalized[0]?.key || "bukhari");
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "To‘plamlarni yuklab bo‘lmadi");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCollectionsLoading(false);
      });

    return () => controller.abort();
  }, []);

  async function openHadith(
    key: string,
    hadithNumber: string
  ) {
    if (!/^[a-z0-9-]{2,50}$/.test(key)) {
      setError("To‘plam nomi noto‘g‘ri");
      return;
    }

    if (!/^[1-9]\d*$/.test(hadithNumber) || Number(hadithNumber) > 100000) {
      setError("1 dan 100000 gacha butun hadis raqamini kiriting");
      return;
    }

    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setCopied(null);
    setError("");
    setHadith(null);
    setSearchMode(false);
    setCollection(key);
    setNumber(hadithNumber);

    try {
      const response = await getData(
        `/api/v1/hadith/${key}/${hadithNumber}`, controller.signal
      );
      if (controller.signal.aborted) return;
      const normalized = normalizeHadith(response, key);

      setHadith({
        ...normalized,
        number: normalized.number || hadithNumber,
        collection: normalized.collection || key
      });
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Xatolik");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function searchHadith() {
    const term = query.trim();

    if (term.length < 2 || term.length > 200) {
      setError("Qidiruv uchun 2 dan 200 gacha belgi kiriting");
      return;
    }

    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setCopied(null);
    setError("");
    setHadith(null);
    setResults([]);
    setSearchMode(true);

    try {
      const response = await getData(
        `/api/v1/hadith/search?q=${encodeURIComponent(term)}`, controller.signal
      );
      if (controller.signal.aborted) return;

      const items = extractResults(response);

      setResults(
        items.map(item => normalizeHadith(item))
      );
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Qidiruv xatosi");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function copyHadith(item: Hadith) {
    const text = [
      `${COLLECTION_NAMES[item.collection] || item.collection} #${item.number}`,
      item.arabic,
      item.english,
      item.grade ? `API bahosi: ${item.grade}` : "",
      "Ma'lumot provayderi: UmmahAPI"
    ].filter(Boolean).join("\n\n");

    try {
      await navigator.clipboard.writeText(text);
      if (copyTimer.current !== null) clearTimeout(copyTimer.current);
      setCopied(item);
      copyTimer.current = setTimeout(() => setCopied(null), 1800);
    } catch {
      setError("Nusxalash amalga oshmadi");
    }
  }


  return (
    <div className="hadith-pro">
      <div className="hadith-heading">
        <span className="eyebrow">ILMUL HADIS</span>
        <h1>Hadis kutubxonasi</h1>
        <p>
          Hadis to‘plamlarini o‘rganing, raqam orqali
          toping va inglizcha matndan qidiring.
        </p>
      </div>

      {error && (
        <div className="quran-error" role="alert">
          <span>{error}</span>
          <button type="button" aria-label="Xatoni yopish" onClick={() => setError("")}>
            ×
          </button>
        </div>
      )}

      <div className="hadith-tools">
        <form
          className="hadith-search"
          onSubmit={e => {
            e.preventDefault();
            searchHadith();
          }}
        >
          <Search size={18} />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search in English: prayer, intention..."
            minLength={2}
            maxLength={200}
            aria-label="Hadis matnidan qidirish"
            required
          />
          <button disabled={loading} type="submit">
            Qidirish
          </button>
        </form>

        <form
          className="hadith-number-form"
          onSubmit={e => {
            e.preventDefault();
            openHadith(collection || "bukhari", number);
          }}
        >
          <select
            aria-label="Hadis to‘plami"
            value={collection || "bukhari"}
            onChange={e => setCollection(e.target.value)}
          >
            {(collections.length
              ? collections
              : [{ key: "bukhari", name: "Sahih al-Bukhari" }]
            ).map(item => (
              <option key={item.key} value={item.key}>
                {item.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={100000}
            step={1}
            value={number}
            onChange={e => setNumber(e.target.value)}
            aria-label="Hadis raqami"
            required
          />
          <button disabled={loading} type="submit">
            Hadisni ochish
          </button>
        </form>
      </div>

      {loading && (
        <div className="loading-state">
          <Loader2 className="spin" />
          Hadislar yuklanmoqda...
        </div>
      )}

      {!loading && hadith && (
        <>
          <button
            className="hadith-back"
            onClick={() => {
              setHadith(null);
              setSearchMode(false);
              setError("");
              setCopied(null);
            }}
          >
            <ArrowLeft size={17} />
            To‘plamlarga qaytish
          </button>
          <HadithCard item={hadith} copied={copied === hadith} onCopy={copyHadith} />
        </>
      )}

      {!loading && searchMode && !hadith && !error && (
        <section className="hadith-results">
          <button
            type="button"
            className="hadith-back"
            onClick={() => {
              setSearchMode(false);
              setError("");
              setCopied(null);
            }}
          >
            <ArrowLeft size={17} />
            To‘plamlarga qaytish
          </button>
          <h2>Qidiruv natijalari ({results.length})</h2>
          {results.length ? (
            results.map((item, index) => (
              <div key={`${item.collection}-${item.number}-${index}`}>
                <HadithCard item={item} copied={copied === item} onCopy={copyHadith} />
                {item.collection &&
                  /^[a-z0-9-]{2,50}$/.test(item.collection) &&
                  /^[1-9]\d*$/.test(item.number) && Number(item.number) <= 100000 && (
                    <button
                      className="hadith-detail-link"
                      onClick={() =>
                        openHadith(item.collection, item.number)
                      }
                    >
                      To‘liq hadisni ochish
                    </button>
                  )}
              </div>
            ))
          ) : (
            <p className="hadith-empty">
              Natija topilmadi. Inglizcha boshqa
              kalit so‘z bilan qayta qidiring.
            </p>
          )}
        </section>
      )}

      {!loading && !hadith && !searchMode && (
        <>
          <h2 className="hadith-section-title">
            <BookOpen size={21} />
            Hadis to‘plamlari
          </h2>

          {collectionsLoading ? (
            <div className="loading-state">
              <Loader2 className="spin" />
              To‘plamlar yuklanmoqda...
            </div>
          ) : (
            <div className="hadith-collections">
              {collections.map(item => (
                <button
                  key={item.key}
                  className="hadith-collection"
                  onClick={() => openHadith(item.key, "1")}
                >
                  <div className="hadith-collection-icon">
                    <BookMarked size={23} />
                  </div>
                  <div className="hadith-collection-body">
                    <h3>{item.name}</h3>
                    {item.arabic_name && (
                      <p dir="rtl" className="hadith-collection-ar">
                        {item.arabic_name}
                      </p>
                    )}
                    <small>
                      {item.author || "Hadis to‘plami"}
                      {item.total_hadiths
                        ? ` · ${item.total_hadiths.toLocaleString()} hadis`
                        : ""}
                    </small>
                  </div>
                </button>
              ))}
            </div>
          )}
          {!collectionsLoading && !collections.length && !error && (
            <p className="hadith-empty">Hozircha to‘plamlar topilmadi.</p>
          )}
        </>
      )}
    </div>
  );
}
