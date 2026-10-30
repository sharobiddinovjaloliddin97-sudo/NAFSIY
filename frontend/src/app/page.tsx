
"use client";

import { useState, useEffect, useRef, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChatSessions } from "@/hooks/useChatSessions";
import QuranReader from "@/components/QuranReader";
import HadithReader from "@/components/HadithReader";
import AuthPanel from "@/components/AuthPanel";
import {
  BookOpen, BookMarked, Clock3, MessageCircle,
  Send, Sparkles, Menu, X, ArrowUpRight, Loader2,
  ChevronRight, MoonStar, ShieldCheck, RotateCcw, Copy, Plus, Trash2
} from "lucide-react";

type Section = "chat" | "quran" | "prayer" | "hadith";

type PrayerTimes = {
  date?: string;
  timezone?: string;
  times?: Record<string, string>;
  data?: { timings?: Record<string, string> };
};

const API = process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const navigation = [
  { id: "chat", label: "NAFSIY AI", icon: MessageCircle },
  { id: "quran", label: "Qur'on", icon: BookOpen },
  { id: "prayer", label: "Namoz vaqtlari", icon: Clock3 },
  { id: "hadith", label: "Hadis", icon: BookMarked },
] as const;

const suggestions = [
  "Islomda ilm olishning fazilati qanday?",
  "Fotiha surasining 1-oyatini tushuntir",
  "Sahih Buxoriy 1-hadisni keltir",
  "Tavakkul nima? Qur'ondan dalil keltir",
];

async function apiGet(path: string) {
  const response = await fetch(`${API}${path}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Ma'lumot olinmadi");
  }
  return data;
}

export default function Home() {
  const [section, setSection] = useState<Section>("chat");
  const [mobileOpen, setMobileOpen] = useState(false);
  const {
    sessions, activeId, messages, loaded, authenticated, loadError,
    createSession, selectSession, deleteSession, reload, captureContext, isCurrentContext
  } = useChatSessions();
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [sessionBusy, setSessionBusy] = useState(false);
  const aiRequest = useRef<AbortController | null>(null);
  const sendVersion = useRef(0);

  const [city, setCity] = useState("Tashkent");
  const [prayer, setPrayer] = useState<PrayerTimes | null>(null);
  const [prayerLoading, setPrayerLoading] = useState(false);

  useEffect(() => {
    if (!loaded || section !== "chat") return;

    chatBottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending, loaded, section, activeId]);

  useEffect(() => {
    const changed = () => {
      sendVersion.current++;
      aiRequest.current?.abort();
      setSending(false);
      setSessionBusy(false);
      setInput("");
      setError("");
    };
    window.addEventListener("nafsiy-auth-changed", changed);
    return () => {
      window.removeEventListener("nafsiy-auth-changed", changed);
      aiRequest.current?.abort();
    };
  }, []);

  async function newSession() {
    if (sending || sessionBusy || !loaded || loadError) return;
    const context = captureContext();
    setSessionBusy(true);
    setError("");
    try {
      await createSession(context);
      setInput("");
      navigate("chat");
    } catch (err) {
      if (isCurrentContext(context)) setError(err instanceof Error ? err.message : "Chat yaratilmadi");
    } finally {
      if (isCurrentContext(context)) setSessionBusy(false);
    }
  }

  async function removeSession(id: string) {
    if (sending || sessionBusy || !loaded || loadError) return;
    if (!window.confirm("Ushbu suhbatni o'chirmoqchimisiz?")) return;
    const context = captureContext();
    setSessionBusy(true);
    try {
      await deleteSession(id, context);
      if (id === activeId) setInput("");
      setError("");
    } catch (err) {
      if (isCurrentContext(context)) setError(err instanceof Error ? err.message : "O‘chirib bo‘lmadi");
    } finally {
      if (isCurrentContext(context)) setSessionBusy(false);
    }
  }

  function navigate(next: Section) {
    setSection(next);
    setMobileOpen(false);
    setError("");
  }

  async function sendMessage(text = input) {
    const question = text.trim();
    if (!question || sending || sessionBusy || !loaded || loadError) return;
    if (!authenticated) {
      window.dispatchEvent(new Event("nafsiy-open-auth"));
      return;
    }

    const context = captureContext();
    const version = ++sendVersion.current;
    const controller = new AbortController();
    aiRequest.current = controller;

    setInput("");
    setSending(true);
    setError("");
    let knownFailure = false;
    let requestStarted = false;

    try {
      const sessionId = activeId || await createSession(context);
      if (!isCurrentContext(context)) return;
      requestStarted = true;
      const response = await fetch(`${API}/api/v1/ai/chats/${encodeURIComponent(sessionId)}/send`, {
        method: "POST",
        credentials: "include",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question })
      });

      const data = await response.json();

      if (!response.ok) {
        knownFailure = true;
        if (response.status === 401) {
          window.dispatchEvent(new Event("nafsiy-auth-changed"));
          window.dispatchEvent(new Event("nafsiy-open-auth"));
        }
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "AI javob bera olmadi"
        );
      }

      if (!isCurrentContext(context)) return;
      await reload();
    } catch (err) {
      if (isCurrentContext(context)) {
        setInput(question);
        setError(knownFailure || !requestStarted
          ? err instanceof Error ? err.message : "AI bilan aloqa xatosi"
          : "So‘rov yakunini tasdiqlab bo‘lmadi. Xabar saqlangan bo‘lishi mumkin. Qayta yuborishdan oldin suhbatni yangilang.");
      }
    } finally {
      if (version === sendVersion.current) setSending(false);
    }
  }

  async function loadPrayer(e?: FormEvent) {
    e?.preventDefault();
    setPrayerLoading(true);
    setError("");
    try {
      const result = await apiGet(
        `/api/v1/prayer/times?city=${encodeURIComponent(city)}&country=Uzbekistan`
      );
      setPrayer(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Xatolik");
    } finally {
      setPrayerLoading(false);
    }
  }

  const sectionTitle = {
    chat: "NAFSIY AI",
    quran: "Qur'oni Karim",
    prayer: "Namoz vaqtlari",
    hadith: "Hadislar"
  };

  const times = prayer?.times || prayer?.data?.timings || {};
  const prayerNames: Record<string, string> = {
    Fajr: "Bomdod",
    Sunrise: "Quyosh chiqishi",
    Dhuhr: "Peshin",
    Asr: "Asr",
    Maghrib: "Shom",
    Isha: "Xufton"
  };

  return (
    <div className="app-shell">
      {mobileOpen && (
        <button
          className="mobile-overlay"
          onClick={() => setMobileOpen(false)}
          aria-label="Menyuni yopish"
        />
      )}

      <aside className={`sidebar ${mobileOpen ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-symbol">
            <MoonStar size={23} strokeWidth={1.7} />
          </div>
          <div>
            <div className="brand-name">NAFSIY</div>
            <div className="brand-subtitle">
              YOUR ISLAMIC COMPANION
            </div>
          </div>
          <button
            className="mobile-close"
            onClick={() => setMobileOpen(false)}
          >
            <X size={20} />
          </button>
        </div>

        <div className="sidebar-group-label">PLATFORM</div>

        <nav className="nav-list">
          {navigation.map(item => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={`nav-link ${
                  section === item.id ? "active" : ""
                }`}
                onClick={() => navigate(item.id)}
              >
                <Icon size={19} strokeWidth={1.8} />
                <span>{item.label}</span>
                {section === item.id && (
                  <ChevronRight size={16} className="nav-chevron" />
                )}
              </button>
            );
          })}
        </nav>

        <div className="chat-history-section">
          <div className="chat-history-header">
            <span>SUHBATLAR</span>
            <button
              type="button"
              disabled={sending || sessionBusy || !loaded || !!loadError}
              onClick={newSession}
              title="Yangi suhbat"
              aria-label="Yangi suhbat"
            >
              <Plus size={17} />
            </button>
          </div>

          <p className="chat-sync-status" role="status">
            {!loaded ? "Suhbatlar yuklanmoqda…" : loadError ? "Sinxronlash to‘xtadi"
              : authenticated ? "Akkauntingiz bulutiga saqlanadi" : "AI chat uchun akkauntga kiring"}
          </p>

          <div className="chat-history-list">
            {sessions.map(session => (
              <div
                key={session.id}
                className={`history-item ${activeId === session.id ? "selected" : ""}`}
              >
                <button
                  type="button"
                  className="history-open"
                  disabled={sending || sessionBusy || !loaded || !!loadError}
                  onClick={() => {
                    selectSession(session.id);
                    setInput("");
                    navigate("chat");
                  }}
                  title={session.title}
                  aria-current={activeId === session.id ? "true" : undefined}
                >
                  <MessageCircle size={15} />
                  <span>{session.title}</span>
                </button>
                <button
                  type="button"
                  className="history-delete"
                  disabled={sending || sessionBusy || !loaded || !!loadError}
                  aria-label={`O'chirish: ${session.title}`}
                  title="Suhbatni o'chirish"
                  onClick={() => removeSession(session.id)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="sidebar-bottom">
          <div className="sidebar-quote">
            <div className="quote-star">✦</div>
            <p>
              Ilm qalbga nur,
              <br />
              amal hayotga ma&apos;no.
            </p>
            <span>NAFSIY • BILIM VA MA&apos;NAVIYAT</span>
          </div>
          <div className="sidebar-footer">
            <ShieldCheck size={15} />
            <span>Islamic knowledge assistant</span>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button
            className="menu-button"
            onClick={() => setMobileOpen(true)}
            aria-label="Menyu"
          >
            <Menu size={22} />
          </button>

          <div className="topbar-path">
            Platform <ChevronRight size={14} />
            <span>{sectionTitle[section]}</span>
          </div>

          <div className="topbar-status">
            <span className="status-dot" />
            AI POWERED
          </div>
          <AuthPanel />
        </header>

        <div className="workspace">
          {loadError && (
            <div className="error-banner" role="alert">
              <span>{loadError}</span>
              <button type="button" onClick={() => { void reload(); }}>Qayta yuklash</button>
            </div>
          )}
          {error && (
            <div className="error-banner">
              <span>{error}</span>
              {section === "chat" && authenticated && (
                <button type="button" onClick={() => { setError(""); void reload(); }}>
                  Suhbatni yangilash
                </button>
              )}
              <button onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}

          {section === "chat" && (
            <div className="chat-layout">
              {messages.length === 0 ? (
                <div className="welcome">
                  <div className="welcome-icon">
                    <MoonStar size={35} strokeWidth={1.35} />
                  </div>
                  <div className="eyebrow">
                    <Sparkles size={14} />
                    ASSALOMU ALAYKUM
                  </div>
                  <h1>
                    Ilm sari yo‘lingiz
                    <br />
                    <span>NAFSIY bilan.</span>
                  </h1>
                  <p className="welcome-description">
                    Qur&apos;on, hadis va islomiy bilimlar haqida
                    savollaringizni bering. NAFSIY sizga
                    manbalar asosida yordam berishga intiladi.
                  </p>
                  <div className="suggestion-grid">
                    {suggestions.map((suggestion, index) => (
                      <button
                        className="suggestion-card"
                        key={suggestion}
                        disabled={!loaded || !authenticated || sending || sessionBusy || !!loadError}
                        onClick={() => sendMessage(suggestion)}
                      >
                        <span className="suggestion-index">
                          0{index + 1}
                        </span>
                        <span>{suggestion}</span>
                        <ArrowUpRight size={17} />
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="message-list">
                  <div className="chat-heading">
                    <div>
                      <span className="eyebrow">SUHBAT</span>
                      <h2>NAFSIY bilan muloqot</h2>
                    </div>
                    <button
                      className="reset-button"
                      disabled={sending || sessionBusy || !loaded || !!loadError}
                      onClick={newSession}
                    >
                      <RotateCcw size={16} />
                      Yangi suhbat
                    </button>
                  </div>

                  {messages.map((message, index) => (
                    <div
                      key={index}
                      className={`message ${
                        message.role === "user"
                          ? "message-user"
                          : "message-ai"
                      }`}
                    >
                      <div className="message-avatar">
                        {message.role === "user" ? (
                          "S"
                        ) : (
                          <MoonStar size={18} />
                        )}
                      </div>
                      <div className="message-body">
                        <div className="message-author">
                          {message.role === "user"
                            ? "Siz"
                            : "NAFSIY AI"}
                        </div>
                        <div
                          className={`message-text${message.role === "assistant" ? " markdown-content" : ""}`}
                          dir="auto"
                        >
                          {message.role === "assistant" ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {message.content}
                            </ReactMarkdown>
                          ) : message.content}
                        </div>
                        {message.role === "assistant" && (
                          <button
                            type="button"
                            className="copy-answer"
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(message.content);
                              } catch {
                                setError("Matnni nusxalab bo‘lmadi");
                              }
                            }}
                          >
                            <Copy size={14} />
                            Nusxalash
                          </button>
                        )}
                        {!!message.sources?.length && (
                          <div className="source-list">
                            {message.sources.map((source, i) => (
                              <div className="source-chip" key={i}>
                                <BookOpen size={13} />
                                {source.type === "quran"
                                  ? "Qur'on"
                                  : "Hadis"}{" "}
                                {source.reference}
                                <span>· {source.provider}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}

                  {sending && (
                    <div className="message message-ai">
                      <div className="message-avatar">
                        <MoonStar size={18} />
                      </div>
                      <div className="message-body">
                        <div className="message-author">NAFSIY AI</div>
                        <div className="thinking">
                          <Loader2 size={18} className="spin" />
                          Javob tayyorlanmoqda...
                        </div>
                      </div>
                    </div>
                  )}
                  <div ref={chatBottomRef} />
                </div>
              )}

              <div className="composer-wrap">
                {loaded && !authenticated && !loadError && (
                  <div className="chat-login-notice">
                    <span>AI bilan suhbatlashish uchun akkauntingizga kiring.</span>
                    <button type="button" onClick={() => window.dispatchEvent(new Event("nafsiy-open-auth"))}>
                      Kirish
                    </button>
                  </div>
                )}
                <form
                  className="composer"
                  onSubmit={e => {
                    e.preventDefault();
                    sendMessage();
                  }}
                >
                  <textarea
                    disabled={!loaded || !authenticated || !!loadError}
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    placeholder="NAFSIY'dan biror narsa so‘rang..."
                    rows={2}
                    maxLength={2000}
                    onKeyDown={e => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        sendMessage();
                      }
                    }}
                  />
                  <div className="composer-bottom">
                    <span>
                      <Sparkles size={14} />
                      Groq AI bilan ishlaydi
                    </span>
                    <button
                      type="submit"
                      disabled={sending || sessionBusy || !loaded || !authenticated || !!loadError || !input.trim()}
                      aria-label="Yuborish"
                    >
                      {sending
                        ? <Loader2 size={18} className="spin" />
                        : <Send size={18} />}
                    </button>
                  </div>
                </form>
                <p className="composer-note">
                  AI xato qilishi mumkin. Diniy hukmlarni
                  mutaxassis bilan tekshiring.
                </p>
              </div>
            </div>
          )}

          {section === "quran" && (
            <div className="content-page">
              <QuranReader />
            </div>
          )}

          {section === "prayer" && (
            <div className="content-page">
              <div className="page-intro">
                <span className="eyebrow">KUNLIK IBODAT</span>
                <h1>Namoz vaqtlari</h1>
                <p>Shaharingiz bo‘yicha namoz vaqtlarini ko‘ring.</p>
              </div>

              <form className="city-form" onSubmit={loadPrayer}>
                <input
                  value={city}
                  onChange={e => setCity(e.target.value)}
                  placeholder="Shahar"
                  required
                />
                <button type="submit" disabled={prayerLoading}>
                  {prayerLoading ? "Yuklanmoqda..." : "Vaqtlarni ko‘rish"}
                </button>
              </form>

              {prayer && (
                <div className="prayer-panel">
                  <div className="prayer-panel-head">
                    <Clock3 size={24} />
                    <div>
                      <h2>{city}, Uzbekistan</h2>
                      <p>
                        {prayer.date || "Namoz vaqtlari"}
                        {prayer.timezone && ` · ${prayer.timezone}`}
                      </p>
                    </div>
                  </div>

                  {Object.entries(prayerNames).map(([key, name]) => (
                    <div className="prayer-row" key={key}>
                      <span>{name}</span>
                      <strong>
                        {String(times[key] || "—").split(" ")[0]}
                      </strong>
                    </div>
                  ))}
                  <p className="section-note">
                    Hisoblash usuli va mahalliy jadval farq qilishi mumkin.
                    Rasmiy mahalliy jadval bilan solishtiring.
                  </p>
                </div>
              )}
            </div>
          )}

          {section === "hadith" && (
            <div className="content-page">
              <HadithReader />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
