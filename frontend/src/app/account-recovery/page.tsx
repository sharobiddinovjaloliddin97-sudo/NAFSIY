"use client";
import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { csrfHeaders, clearCsrf } from "@/lib/csrf";
const API = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export default function Recovery() {
  const [purpose, setPurpose] = useState("reset");
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let timer: number | undefined;
    const readLink = () => {
      const [kind, value] = window.location.hash.slice(1).split(":");
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (["reset", "verify"].includes(kind) && value) {
          setPurpose(kind); setToken(value); setNotice("");
        }
        window.history.replaceState(null, "", window.location.pathname);
      }, 0);
    };
    readLink();
    window.addEventListener("hashchange", readLink);
    return () => { window.clearTimeout(timer); window.removeEventListener("hashchange", readLink); };
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const headers = await csrfHeaders(true);
      const response = await fetch(`${API}/api/v1/auth/recovery/${purpose}/${token ? "confirm" : "request"}`, {
        method: "POST", credentials: "include", cache: "no-store", signal: AbortSignal.timeout(15000),
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(token ? { token, ...(purpose === "reset" ? { password } : {}) } : { email }),
      });
      if (!response.ok) { clearCsrf(); throw new Error("Request failed or link expired. Try again or request a new link."); }
      setNotice(token ? (purpose === "verify" ? "Completed. Email tasdiqlandi! Endi tizimga kirishingiz mumkin." : "Completed. Sign in again.") : "If eligible, account instructions will be sent.");
      setToken(""); setPassword("");
    } catch { setNotice("Request failed or link expired. Try again or request a new link."); }
    finally { setBusy(false); }
  }
  return <main style={{ maxWidth: 480, margin: "4rem auto", padding: 24 }}>
    <h1>{token && purpose === "verify" ? "Emailni tasdiqlash" : "Account recovery"}</h1>
    <p>{token && purpose === "verify" ? "Emailingizni tasdiqlash uchun quyidagi 'Confirm' tugmasini bosing:" : "Links expire and can be used once. Your password is never sent by email."}</p>
    <form onSubmit={submit}>
      {!token && <><label>Action <select value={purpose} onChange={e => setPurpose(e.target.value)}><option value="reset">Reset password</option><option value="verify">Verify email</option></select></label>
      <label>Email <input type="email" required maxLength={320} value={email} onChange={e => setEmail(e.target.value)} /></label></>}
      {token && purpose === "reset" && <label>New password <input type="password" autoComplete="new-password" minLength={8} maxLength={128} required value={password} onChange={e => setPassword(e.target.value)} /></label>}
      <button disabled={busy}>{busy ? "Please wait" : token ? "Confirm" : "Send instructions"}</button>
    </form><p role="status">{notice}</p><Link href="/">Back to NAFSIY / Bosh sahifaga qaytish</Link>
  </main>;
}
