"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  UserRound,
  LogIn,
  LogOut,
  X,
  Loader2,
  ShieldCheck
} from "lucide-react";

const API =
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

type User = {
  id: string;
  email: string;
  is_active: boolean;
};

class AuthRequestError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function readUser(value: unknown): User {
  if (value && typeof value === "object" && "id" in value && "email" in value &&
    "is_active" in value && typeof value.id === "string" &&
    typeof value.email === "string" && typeof value.is_active === "boolean") {
    return { id: value.id, email: value.email, is_active: value.is_active };
  }
  throw new Error("Akkaunt ma’lumotini olishning iloji bo‘lmadi");
}

async function authRequest(
  path: string,
  options: RequestInit = {}
): Promise<unknown> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(
    `${API}/api/v1/auth${path}`,
    {
      ...options,
      credentials: "include",
      cache: "no-store",
      signal: options.signal
        ? AbortSignal.any([options.signal, AbortSignal.timeout(15000)])
        : AbortSignal.timeout(15000),
      headers
    }
  );

  const data: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Email yoki parol noto‘g‘ri",
      403: "Bu so‘rovga ruxsat berilmadi",
      409: "Bu email bilan akkaunt allaqachon yaratilgan",
      422: "Email va parolni tekshiring",
      429: "Juda ko‘p urinish. Birozdan keyin qayta urinib ko‘ring",
    };
    throw new AuthRequestError(response.status,
      messages[response.status] || "Xizmat vaqtincha mavjud emas. Qayta urinib ko‘ring");
  }

  return data;
}

export default function AuthPanel() {
  const [user, setUser] = useState<User | null>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<
    "login" | "register"
  >("login");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const modalRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const authChannel = useRef<BroadcastChannel | null>(null);

  useEffect(() => {
    const openLogin = () => {
      setMode("login");
      setError("");
      setOpen(true);
    };
    window.addEventListener("nafsiy-open-auth", openLogin);
    return () => window.removeEventListener("nafsiy-open-auth", openLogin);
  }, []);

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel("nafsiy-auth");
    authChannel.current = channel;
    channel.onmessage = () => window.dispatchEvent(new Event("nafsiy-auth-changed"));
    return () => {
      channel.close();
      authChannel.current = null;
    };
  }, []);

  function notifyAuthChanged() {
    window.dispatchEvent(new Event("nafsiy-auth-changed"));
    authChannel.current?.postMessage("changed");
  }

  useEffect(() => {
    let controller: AbortController;
    function check() {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      void authRequest("/me", { signal: request.signal })
      .then(data => {
        if (!request.signal.aborted) setUser(readUser(data));
      })
      .catch(err => {
        if (request.signal.aborted) return;
        if (err instanceof AuthRequestError && err.status === 401) {
          setUser(null);
        } else {
          setError("Akkauntni tekshirib bo‘lmadi. Qayta kirishga urinib ko‘ring");
        }
      })
      .finally(() => {
        if (!request.signal.aborted) setChecking(false);
      });
    }
    check();
    window.addEventListener("nafsiy-auth-changed", check);
    return () => {
      controller?.abort();
      window.removeEventListener("nafsiy-auth-changed", check);
    };
  }, []);

  useEffect(() => () => requestRef.current?.abort(), []);

  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const target = modalRef.current?.querySelector<HTMLElement>("input, .auth-submit")
      ?? modalRef.current?.querySelector<HTMLElement>(".auth-close");
    target?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      trigger?.focus();
    };
  }, [open]);

  function closePanel() {
    if (loading) return;
    setOpen(false);
    setPassword("");
    setError("");
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closePanel();
    }
    if (event.key !== "Tab") return;
    const buttons = modalRef.current?.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled)"
    );
    if (!buttons?.length) {
      event.preventDefault();
      return;
    }
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (loading) return;
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError("");

    try {
      const data = await authRequest(
        mode === "login" ? "/login" : "/register",
        {
          method: "POST",
          signal: controller.signal,
          body: JSON.stringify({
            email: email.trim(),
            password
          })
        }
      );

      if (controller.signal.aborted) return;
      setUser(readUser(data));
      notifyAuthChanged();
      setPassword("");
      setOpen(false);
    } catch (err) {
      if (!controller.signal.aborted) setError(
        err instanceof AuthRequestError ? err.message : "Server bilan aloqa o‘rnatilmadi. Qayta urinib ko‘ring"
      );
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function logout() {
    if (loading) return;
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError("");

    try {
      await authRequest("/logout", {
        method: "POST",
        signal: controller.signal
      });
      if (controller.signal.aborted) return;

      setUser(null);
      notifyAuthChanged();
      setEmail("");
      setPassword("");
      setOpen(false);
    } catch (err) {
      if (!controller.signal.aborted) setError(
        err instanceof AuthRequestError ? err.message : "Server bilan aloqa o‘rnatilmadi. Qayta urinib ko‘ring"
      );
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return (
    <div className="auth-widget">
      <button
        type="button"
        className="auth-trigger"
        ref={triggerRef}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={user?.email || "Kirish"}
        onClick={() => setOpen(true)}
        disabled={checking || loading}
      >
        <UserRound size={17} />
        <span>
          {checking
            ? "Yuklanmoqda"
            : user
              ? user.email
              : "Kirish"}
        </span>
      </button>

      {open && (
        <div
          className="auth-overlay"
          onMouseDown={e => {
            if (e.target === e.currentTarget) {
              closePanel();
            }
          }}
        >
          <div
            className="auth-modal"
            ref={modalRef}
            onKeyDown={handleDialogKeyDown}
            aria-busy={loading}
            role="dialog"
            aria-modal="true"
            aria-label="NAFSIY akkaunti"
          >
            <button
              type="button"
              className="auth-close"
              onClick={closePanel}
              disabled={loading}
              aria-label="Yopish"
            >
              <X size={20} />
            </button>

            <div className="auth-symbol">
              <ShieldCheck size={27} />
            </div>

            {user ? (
              <>
                <h2>Shaxsiy akkaunt</h2>
                <p className="auth-subtitle">
                  NAFSIY&apos;ga xush kelibsiz!
                </p>
                <div className="auth-account-email">
                  {user.email}
                </div>
                {error && (
                  <p className="auth-error" role="alert">{error}</p>
                )}
                <button
                  className="auth-submit"
                  type="button"
                  onClick={logout}
                  disabled={loading}
                >
                  {loading ? <Loader2 size={17} className="spin" /> : <LogOut size={17} />}
                  Akkauntdan chiqish
                </button>
              </>
            ) : (
              <>
                <h2>
                  {mode === "login"
                    ? "Xush kelibsiz"
                    : "Akkaunt yaratish"}
                </h2>
                <p className="auth-subtitle">
                  NAFSIY — Islamic AI Companion
                </p>

                <form onSubmit={submit}>
                  <label>
                    Email
                    <input
                      type="email"
                      value={email}
                      onChange={e =>
                        setEmail(e.target.value)
                      }
                      placeholder="name@example.com"
                      autoComplete="email"
                      maxLength={320}
                      disabled={loading}
                      required
                    />
                  </label>

                  <label>
                    Parol
                    <input
                      type="password"
                      value={password}
                      onChange={e =>
                        setPassword(e.target.value)
                      }
                      placeholder={mode === "register" ? "Kamida 8 belgi" : "Parolingiz"}
                      minLength={mode === "register" ? 8 : 1}
                      maxLength={128}
                      disabled={loading}
                      autoComplete={
                        mode === "login"
                          ? "current-password"
                          : "new-password"
                      }
                      required
                    />
                  </label>

                  {error && (
                    <p className="auth-error" role="alert">{error}</p>
                  )}

                  <button
                    className="auth-submit"
                    disabled={loading}
                    type="submit"
                  >
                    {loading ? (
                      <Loader2
                        size={17}
                        className="spin"
                      />
                    ) : (
                      <LogIn size={17} />
                    )}
                    {mode === "login"
                      ? "Kirish"
                      : "Ro‘yxatdan o‘tish"}
                  </button>
                </form>

                <button
                  type="button"
                  className="auth-switch"
                  disabled={loading}
                  onClick={() => {
                    setMode(
                      mode === "login"
                        ? "register"
                        : "login"
                    );
                    setError("");
                    setPassword("");
                  }}
                >
                  {mode === "login"
                    ? "Akkauntingiz yo‘qmi? Ro‘yxatdan o‘ting"
                    : "Akkauntingiz bormi? Kirish"}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
