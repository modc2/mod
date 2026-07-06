"use client";

import {
  createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode,
} from "react";

const DOCKED_KEY = "ct_sidebar_docked";
const WIDTH_KEY = "ct_sidebar_width";

export const SIDEBAR_MIN = 280;
export const SIDEBAR_MAX = 1600;
export const SIDEBAR_DEFAULT = 420;

interface SidebarContextValue {
  docked: boolean;
  width: number;
  hydrated: boolean;
  toggleDocked: () => void;
  setDocked: (v: boolean) => void;
  setWidth: (v: number) => void;
  /// Begin a drag to resize. Component should pass a MouseDown handler.
  startDrag: (e: React.MouseEvent) => void;
}

const SidebarContext = createContext<SidebarContextValue | null>(null);

export function useSidebar() {
  const ctx = useContext(SidebarContext);
  if (!ctx) throw new Error("useSidebar must be used inside <SidebarProvider>");
  return ctx;
}

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [docked, setDockedState] = useState(false);
  const [width, setWidthState] = useState(SIDEBAR_DEFAULT);
  const [hydrated, setHydrated] = useState(false);
  const draggingRef = useRef(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(DOCKED_KEY) === "1") setDockedState(true);
      const w = Number(localStorage.getItem(WIDTH_KEY));
      if (Number.isFinite(w) && w >= SIDEBAR_MIN && w <= SIDEBAR_MAX) setWidthState(w);
    } catch {}
    setHydrated(true);
  }, []);

  const setDocked = useCallback((v: boolean) => {
    setDockedState(v);
    try { localStorage.setItem(DOCKED_KEY, v ? "1" : "0"); } catch {}
  }, []);

  const toggleDocked = useCallback(() => setDocked(!docked), [docked, setDocked]);

  const setWidth = useCallback((w: number) => {
    const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w));
    setWidthState(clamped);
    try { localStorage.setItem(WIDTH_KEY, String(clamped)); } catch {}
  }, []);

  // Global drag listener — installed once. The sidebar component triggers
  // `startDrag` on mousedown over its handle.
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const fromRight = window.innerWidth - e.clientX;
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, fromRight));
      setWidthState(next);
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      try { localStorage.setItem(WIDTH_KEY, String(width)); } catch {}
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [width]);

  const startDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  return (
    <SidebarContext.Provider value={{ docked, width, hydrated, toggleDocked, setDocked, setWidth, startDrag }}>
      {children}
    </SidebarContext.Provider>
  );
}
