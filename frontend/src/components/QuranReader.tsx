"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import BookmarkButton from "@/components/BookmarkButton";
import {
  ArrowLeft,
  BookOpen,
  Check,
  Copy,
  Loader2,
  Search,
  X
} from "lucide-react";

const API =
  process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:8000");

type Surah = {
  number: number;
  name: string;
  englishName: string;
  numberOfAyahs: number;
};

type Ayah = {
  numberInSurah: number;
  text: string;
};

type SurahDetail = Surah & {
  ayahs: Ayah[];
};

type Language = "ar" | "uz" | "en" | "ru";

const editions = {
  ar: {
    label: "Faqat arabcha",
    code: "",
    language: ""
  },
  en: {
    label: "English — Talal Itani (ClearQuran.com)",
    code: "en.itani",
    language: "en"
  },
  ru: {
    label: "Русский — Кулиев",
    code: "ru.kuliev",
    language: "ru"
  },
  uz: {
    label: "O‘zbekcha — ruxsat kutilmoqda",
    code: "",
    language: "uz"
  }
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validSurah(value: unknown): value is Surah {
  return isRecord(value) && typeof value.number === "number" &&
    Number.isInteger(value.number) && value.number >= 1 && value.number <= 114 &&
    typeof value.name === "string" && typeof value.englishName === "string" &&
    typeof value.numberOfAyahs === "number" && Number.isInteger(value.numberOfAyahs) &&
    value.numberOfAyahs > 0;
}

function unwrap(value: unknown): unknown {
  let result = value;

  for (let i = 0; i < 3; i++) {
    if (
      isRecord(result) &&
      result.data &&
      !result.ayahs
    ) {
      result = result.data;
    } else {
      break;
    }
  }

  return result;
}

function extractAyahs(value: unknown, translated = false): Ayah[] {
  const root = unwrap(value);

  const possible = [
    root,
    ...(isRecord(root) ? [root.surah, root.translation, root.translations, root.edition] : [])
  ];

  for (const entry of possible) {
    const item = unwrap(entry);

    const ayahs = Array.isArray(item)
      ? item
      : isRecord(item) ? item.ayahs : null;

    if (Array.isArray(ayahs)) {
      return ayahs.flatMap((ayah: unknown) => {
        if (!isRecord(ayah)) return [];
        const number = ayah.numberInSurah ?? ayah.number;
        const text = translated ? ayah.translation ?? ayah.text : ayah.arabic ?? ayah.text;
        return typeof number === "number" && Number.isInteger(number) && number > 0 &&
          typeof text === "string" && text.trim()
          ? [{ numberInSurah: number, text }] : [];
      });
    }
  }

  return [];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Ma’lumotni yuklab bo‘lmadi";
}

async function getJSON(
  path: string,
  signal?: AbortSignal
): Promise<unknown> {
  const response = await fetch(`${API}${path}`, {
    signal
  });

  const data: unknown = await response.json();
  if (!response.ok) {
    throw new Error(
      isRecord(data) && typeof data.detail === "string"
        ? data.detail : `API xatosi: HTTP ${response.status}`
    );
  }

  return data;
}

export default function QuranReader() {
  const [surahs, setSurahs] = useState<Surah[]>([]);
  const [selected, setSelected] =
    useState<SurahDetail | null>(null);

  const [translations, setTranslations] =
    useState<Ayah[]>([]);

  const [language, setLanguage] =
    useState<Language>("ar");

  const [surahSearch, setSurahSearch] = useState("");
  const [ayahSearch, setAyahSearch] = useState("");
  const [jump, setJump] = useState("");

  const [loading, setLoading] = useState(true);
  const [readingLoading, setReadingLoading] =
    useState(false);

  const [translationLoading, setTranslationLoading] =
    useState(false);

  const [error, setError] = useState("");
  const [copied, setCopied] = useState<number | null>(null);

  const readerRef = useRef<HTMLDivElement>(null);
  const ayahRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const surahRequest = useRef<AbortController | null>(null);
  const translationRequest = useRef<AbortController | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    surahRequest.current?.abort();
    translationRequest.current?.abort();
    if (copyTimer.current !== null) clearTimeout(copyTimer.current);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    getJSON("/api/v1/quran/surahs", controller.signal)
      .then(data => {
        const result = unwrap(data);

        if (!Array.isArray(result) || !result.length || !result.every(validSurah)) {
          throw new Error("Suralar formati noto‘g‘ri");
        }

        if (!controller.signal.aborted) setSurahs(result);
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(errorMessage(err));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, []);

  async function loadTranslation(nextLanguage: Language) {
    translationRequest.current?.abort();
    setLanguage(nextLanguage);
    setTranslations([]);
    setError("");
    if (!selected || nextLanguage === "ar") {
      setTranslationLoading(false);
      return;
    }

    const controller = new AbortController();
    translationRequest.current = controller;
    const config = editions[nextLanguage];

    setTranslationLoading(true);

    const path =
      `/api/v1/quran/surahs/${selected.number}/read` +
      `?language=${config.language}` +
      `&edition=${config.code}`;

    try {
      const data = await getJSON(path, controller.signal);
      if (controller.signal.aborted) return;
      const ayahs = extractAyahs(data, true);

      if (!ayahs.length) {
        throw new Error("Tarjima formati tanilmadi yoki tarjima topilmadi");
      }

      if (ayahs.length !== selected.ayahs.length || ayahs.some(
        (ayah, index) => ayah.numberInSurah !== selected.ayahs[index].numberInSurah
      )) {
        throw new Error("Tarjima oyatlari asl matnga mos kelmadi");
      }

      setTranslations(ayahs);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      if (!controller.signal.aborted) setTranslationLoading(false);
    }
  }

  const filteredSurahs = useMemo(() => {
    const q = surahSearch.trim().toLowerCase();

    if (!q) return surahs;

    return surahs.filter(surah =>
      String(surah.number) === q ||
      surah.englishName.toLowerCase().includes(q) ||
      surah.name.includes(surahSearch.trim())
    );
  }, [surahs, surahSearch]);

  const translationMap = useMemo(
    () =>
      new Map(
        translations.map(ayah => [
          ayah.numberInSurah,
          ayah.text
        ])
      ),
    [translations]
  );

  const filteredAyahs = useMemo(() => {
    if (!selected) return [];

    const q = ayahSearch.trim().toLowerCase();

    if (!q) return selected.ayahs;

    return selected.ayahs.filter(ayah => {
      const translated =
        translationMap.get(ayah.numberInSurah) || "";

      return (
        String(ayah.numberInSurah) === q ||
        ayah.text.includes(ayahSearch.trim()) ||
        translated.toLowerCase().includes(q)
      );
    });
  }, [selected, ayahSearch, translationMap]);

  async function openSurah(number: number) {
    surahRequest.current?.abort();
    translationRequest.current?.abort();
    const controller = new AbortController();
    surahRequest.current = controller;
    setReadingLoading(true);
    setTranslationLoading(false);
    setCopied(null);
    setSelected(null);
    setTranslations([]);
    setAyahSearch("");
    setLanguage("ar");
    setError("");

    try {
      const result = unwrap(
        await getJSON(`/api/v1/quran/surahs/${number}`, controller.signal)
      );
      if (controller.signal.aborted) return;

      const ayahs = extractAyahs(result);

      if (!isRecord(result) || !ayahs.length || ayahs.some(
        (ayah, index) => ayah.numberInSurah !== index + 1
      )) {
        throw new Error("Oyatlarni yuklab bo‘lmadi");
      }

      const summary = surahs.find(s => s.number === number);
      if (summary && summary.numberOfAyahs !== ayahs.length) {
        throw new Error("Sura oyatlari soni mos kelmadi");
      }

      setSelected({
        number,
        name: typeof result.name === "string" ? result.name : summary?.name || "",
        englishName:
          (typeof result.englishName === "string" ? result.englishName : "") ||
          summary?.englishName ||
          `Sura ${number}`,
        numberOfAyahs: ayahs.length,
        ayahs
      });

      setJump("");

      window.scrollTo({
        top: 0,
        behavior: "smooth"
      });
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      if (!controller.signal.aborted) setReadingLoading(false);
    }
  }

  function jumpToAyah() {
    const number = Number(jump);

    if (!selected) return;

    if (
      !Number.isInteger(number) ||
      number < 1 ||
      number > selected.ayahs.length
    ) {
      setError("Oyat raqami noto‘g‘ri");
      return;
    }

    setError("");
    setAyahSearch("");

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        ayahRefs.current[number]?.scrollIntoView({
          behavior: "smooth",
          block: "center"
        });
      });
    });
  }

  async function copyAyah(ayah: Ayah) {
    if (!selected) return;

    const translation =
      translationMap.get(ayah.numberInSurah);

    const text = [
      `${selected.englishName} ${selected.number}:${ayah.numberInSurah}`,
      ayah.text,
      translation || "",
      translation
        ? `Tarjima: ${editions[language].label}`
        : ""
    ].filter(Boolean).join("\n\n");

    try {
      await navigator.clipboard.writeText(text);
      if (copyTimer.current !== null) clearTimeout(copyTimer.current);
      setCopied(ayah.numberInSurah);
      copyTimer.current = setTimeout(() => setCopied(null), 1800);
    } catch {
      setError("Nusxalab bo‘lmadi");
    }
  }

  return (
    <div className="quran-pro" ref={readerRef}>
      <div className="quran-pro-heading">
        <span className="eyebrow">AL-QUR&apos;AN AL-KARIM</span>
        <h1>Qur&apos;oni Karim</h1>
        <p>
          Arabcha asl matn, tarjimalar va qulay qidiruv.
        </p>
      </div>

      {error && (
        <div className="quran-error">
          <span>{error}</span>
          <button type="button" aria-label="Xatoni yopish" onClick={() => setError("")}>
            <X size={16} />
          </button>
        </div>
      )}

      {selected === null ? (
        <>
          <div className="quran-search">
            <Search size={19} />
            <input
              value={surahSearch}
              onChange={e => setSurahSearch(e.target.value)}
              placeholder="Sura nomi yoki raqamini kiriting..."
              aria-label="Sura qidirish"
            />
          </div>

          <div className="quran-count">
            {filteredSurahs.length} ta sura
          </div>

          {loading || readingLoading ? (
            <div className="loading-state">
              <Loader2 className="spin" />
              Yuklanmoqda...
            </div>
          ) : (
            <div className="quran-surah-grid">
              {filteredSurahs.map(surah => (
                <button
                  key={surah.number}
                  className="quran-surah-card"
                  onClick={() => openSurah(surah.number)}
                >
                  <span className="quran-surah-number">
                    {surah.number}
                  </span>
                  <span className="quran-surah-info">
                    <strong>{surah.englishName}</strong>
                    <small>
                      {surah.numberOfAyahs} oyat
                    </small>
                  </span>
                  <span className="quran-surah-arabic" dir="rtl">
                    {surah.name}
                  </span>
                </button>
              ))}
            </div>
          )}
          {!loading && !readingLoading && !filteredSurahs.length && !error && (
            <p className="quran-empty">Bu so‘rov bo‘yicha sura topilmadi.</p>
          )}
        </>
      ) : (
        <div className="quran-reading">
          <button
            className="quran-back"
            onClick={() => {
              translationRequest.current?.abort();
              setSelected(null);
              setTranslations([]);
              setTranslationLoading(false);
              setCopied(null);
              setError("");
            }}
          >
            <ArrowLeft size={17} />
            Barcha suralar
          </button>

          <div className="quran-reading-head">
            <BookOpen size={25} />
            <h2>{selected.englishName}</h2>
            <div className="quran-arabic-name" dir="rtl">
              {selected.name}
            </div>
            <p>{selected.numberOfAyahs} oyat</p>
          </div>

          <div className="quran-toolbar">
            <label>
              <span>Tarjima</span>
              <select
                value={language}
                onChange={e =>
                  loadTranslation(e.target.value as Language)
                }
              >
                {Object.entries(editions).map(
                  ([key, config]) => (
                    <option key={key} value={key} disabled={key === "uz" || key === "ru"}>
                      {config.label}{key === "ru" ? " — ruxsat kutilmoqda" : ""}
                    </option>
                  )
                )}
              </select>
            </label>

            <label>
              <span>Oyatga o‘tish</span>
              <div className="quran-jump">
                <input
                  type="number"
                  min={1}
                  max={selected.numberOfAyahs}
                  value={jump}
                  placeholder="Raqam"
                  onChange={e => setJump(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter") jumpToAyah();
                  }}
                />
                <button onClick={jumpToAyah}>
                  O‘tish
                </button>
              </div>
            </label>
          </div>

          <div className="quran-search quran-ayah-search">
            <Search size={18} />
            <input
              value={ayahSearch}
              onChange={e => setAyahSearch(e.target.value)}
              placeholder="Oyat raqami yoki matnidan qidirish..."
              aria-label="Oyat qidirish"
            />
          </div>

          {translationLoading && (
            <div className="loading-state">
              <Loader2 className="spin" />
              Tarjima yuklanmoqda...
            </div>
          )}

          <div className="quran-ayah-list">
            {filteredAyahs.map(ayah => {
              const translation =
                translationMap.get(ayah.numberInSurah);

              return (
                <div
                  key={ayah.numberInSurah}
                  id={`ayah-${ayah.numberInSurah}`}
                  ref={element => {
                    ayahRefs.current[ayah.numberInSurah] =
                      element;
                  }}
                  className="quran-ayah-card"
                >
                  <div className="quran-ayah-actions">
                    <span className="quran-ayah-index">
                      {selected.number}:{ayah.numberInSurah}
                    </span>

                    <div className="reader-actions">
                    <BookmarkButton
                      kind="quran"
                      reference={`${selected.number}:${ayah.numberInSurah}`}
                      title={`${selected.englishName} ${selected.number}:${ayah.numberInSurah}`}
                    />
                    <button
                      title="Oyatni nusxalash"
                      aria-label={`${selected.number}:${ayah.numberInSurah} oyatni nusxalash`}
                      onClick={() => copyAyah(ayah)}
                    >
                      {copied === ayah.numberInSurah ? (
                        <Check size={17} />
                      ) : (
                        <Copy size={17} />
                      )}
                    </button>
                    </div>
                  </div>

                  <p className="quran-ayah-arabic" dir="rtl">
                    {ayah.text}
                  </p>
                  <small>Arabcha: Uthmani · AlQuran Cloud · <a href="https://tanzil.net" target="_blank" rel="noreferrer">Tanzil Quran Text © 2007–2021 Tanzil Project · CC BY 3.0</a> / Quran Academy. Matn mosligi diniy izohni tasdiqlamaydi.</small>

                  {language !== "ar" && translation && (
                    <div className="quran-translation">
                      <p>{translation}</p>
                      <small>
                        {editions[language].label}
                        {language === "en" && <> · <a href="https://clearquran.com/download/" target="_blank" rel="noreferrer">Translation by Talal Itani, ClearQuran.com · CC BY-ND 4.0</a></>}
                      </small>
                    </div>
                  )}
                </div>
              );
            })}

            {!filteredAyahs.length && (
              <p className="quran-empty">
                Bu so‘rov bo‘yicha oyat topilmadi.
              </p>
            )}
          </div>

          <p className="quran-disclaimer">
            Ma&apos;lumot manbasi: AlQuran Cloud. Tarjimalar
            inson talqini bo‘lib, arabcha asl matnning
            o‘rnini bosmaydi.
          </p>
        </div>
      )}
    </div>
  );
}
