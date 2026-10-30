"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { currentAccount, userRequest } from "@/lib/userData";

export type Source = {
  type: string;
  reference: string;
  provider: string;
  verified?: boolean;
};

export type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
};

export type ChatSession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: Message[];
};

type State = {
  sessions: ChatSession[];
  activeId: string;
  loaded: boolean;
  authenticated: boolean;
  loadError: string;
};

const INITIAL: State = { sessions: [], activeId: "", loaded: false, authenticated: false, loadError: "" };
const EMPTY_MESSAGES: Message[] = [];

function guestSession(): ChatSession {
  const now = new Date().toISOString();
  return { id: crypto.randomUUID(), title: "Yangi suhbat", createdAt: now, updatedAt: now, messages: [] };
}

function readChat(value: unknown): ChatSession {
  if (!value || typeof value !== "object" || !("id" in value) || typeof value.id !== "string" ||
    !("title" in value) || typeof value.title !== "string" || !("messages" in value) ||
    !Array.isArray(value.messages) || !("createdAt" in value) || typeof value.createdAt !== "string" ||
    !("updatedAt" in value) || typeof value.updatedAt !== "string") {
    throw new Error("Suhbat ma’lumotlari formati noto‘g‘ri");
  }
  return value as ChatSession;
}

export function useChatSessions() {
  const [state, setState] = useState<State>(INITIAL);
  const stateRef = useRef<State>(INITIAL);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const commit = useCallback((update: State | ((previous: State) => State)) => {
    const next = typeof update === "function" ? update(stateRef.current) : update;
    stateRef.current = next;
    setState(next);
  }, []);

  const reload = useCallback(async () => {
    const version = ++generation.current;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    commit(INITIAL);
    try {
      const user = await currentAccount(request.signal);
      if (version !== generation.current) return;
      if (!user) {
        const guest = guestSession();
        commit({ ...INITIAL, loaded: true, sessions: [guest], activeId: guest.id });
        return;
      }
      const data = await userRequest<unknown>("/chats", { signal: request.signal });
      if (version !== generation.current) return;
      if (!Array.isArray(data)) throw new Error("Suhbatlar javobi noto‘g‘ri");
      const chats = data.map(readChat);
      commit({ ...INITIAL, loaded: true, authenticated: true, sessions: chats, activeId: chats[0]?.id || "" });
    } catch (error) {
      if (version !== generation.current || request.signal.aborted) return;
      commit({ ...INITIAL, loaded: true, loadError: error instanceof Error ? error.message : "Suhbatlar yuklanmadi" });
    }
  }, [commit]);

  const cancel = useCallback(() => {
    generation.current++;
    controller.current?.abort();
  }, []);

  useEffect(() => {
    const frame = requestAnimationFrame(() => { void reload(); });
    const changed = () => { void reload(); };
    window.addEventListener("nafsiy-auth-changed", changed);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("nafsiy-auth-changed", changed);
      cancel();
    };
  }, [reload, cancel]);

  const captureContext = useCallback(() => generation.current, []);
  const isCurrentContext = useCallback((version: number) => generation.current === version, []);

  const ready = useCallback((version: number) => {
    if (version !== generation.current || !stateRef.current.loaded || stateRef.current.loadError) {
      throw new Error("Akkaunt o‘zgardi yoki suhbatlar hali yuklanmadi");
    }
    return stateRef.current;
  }, []);

  const createSession = useCallback(async (version = generation.current): Promise<string> => {
    const current = ready(version);
    if (current.sessions.length >= 50) throw new Error("50 ta suhbat chegarasiga yetildi. Avval eski suhbatni o‘chiring");
    const session = current.authenticated
      ? readChat(await userRequest<unknown>("/chats", {
          method: "POST", body: JSON.stringify({ title: "Yangi suhbat" }), signal: controller.current?.signal,
        }))
      : guestSession();
    ready(version);
    commit(previous => ({ ...previous, sessions: [session, ...previous.sessions], activeId: session.id }));
    return session.id;
  }, [commit, ready]);

  const selectSession = useCallback((id: string) => {
    commit(previous => previous.sessions.some(item => item.id === id) ? { ...previous, activeId: id } : previous);
  }, [commit]);

  const deleteSession = useCallback(async (id: string, version = generation.current) => {
    const current = ready(version);
    if (current.authenticated) {
      await userRequest(`/chats/${encodeURIComponent(id)}`, { method: "DELETE", signal: controller.current?.signal });
    }
    ready(version);
    commit(previous => {
      const sessions = previous.sessions.filter(item => item.id !== id);
      if (!sessions.length && !previous.authenticated) {
        const guest = guestSession();
        return { ...previous, sessions: [guest], activeId: guest.id };
      }
      return { ...previous, sessions, activeId: previous.activeId === id ? sessions[0]?.id || "" : previous.activeId };
    });
  }, [commit, ready]);

  return {
    ...state,
    messages: state.sessions.find(item => item.id === state.activeId)?.messages ?? EMPTY_MESSAGES,
    createSession, selectSession, deleteSession, reload, captureContext, isCurrentContext,
  };
}
