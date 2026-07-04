"use client";

import {
  createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode,
} from "react";

const COLLAPSED_KEY = "poly8bit_strats_sidebar_collapsed";
const WIDTH_KEY = "poly8bit_strats_sidebar_width";

export const SIDEBAR_MIN = 280;
export const SIDEBAR_MAX = 1600;
export const SIDEBAR_DEFAULT = 420;

interface SidebarContextValue {
  /// The account sidebar (wallet / funding / go-live checklist) is permanent —
  /// it always mounts, there's no "undocked" state that drops it back into
  /// the main column. `collapsed` only toggles a thin icon rail vs the full
  /// panel, the same way the left StratSidebar collapses.
  collapsed: boolean;
  width: number;
  hydrated: boolean;
  toggleCollapsed: () => void;
  setCollapsed: (v: boolean) => void;
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
  const [collapsed, setCollapsedState] = useState(false);
  const [width, setWidthState] = useState(SIDEBAR_DEFAULT);
  const [hydrated, setHydrated] = useState(false);
  const draggingRef = useRef(false);
  // Delta-based drag (mouse-x movement since drag start) rather than
  // distance-from-viewport-edge — the panel sits to the left of main content
  // now, not pinned to the screen's right edge, so an edge-relative formula
  // would resize wrong as soon as the left nav rail's own width changes.
  const dragStartXRef = useRef(0);
  const dragStartWidthRef = useRef(SIDEBAR_DEFAULT);

  useEffect(() => {
    try {
      if (localStorage.getItem(COLLAPSED_KEY) === "1") setCollapsedState(true);
      const w = Number(localStorage.getItem(WIDTH_KEY));
      if (Number.isFinite(w) && w >= SIDEBAR_MIN && w <= SIDEBAR_MAX) setWidthState(w);
    } catch {}
    setHydrated(true);
  }, []);

  const setCollapsed = useCallback((v: boolean) => {
    setCollapsedState(v);
    try { localStorage.setItem(COLLAPSED_KEY, v ? "1" : "0"); } catch {}
  }, []);

  const toggleCollapsed = useCallback(() => setCollapsed(!collapsed), [collapsed, setCollapsed]);

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
      // Handle sits on the panel's right edge — dragging right widens it.
      const delta = e.clientX - dragStartXRef.current;
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, dragStartWidthRef.current + delta));
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
    dragStartXRef.current = e.clientX;
    dragStartWidthRef.current = width;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [width]);

  return (
    <SidebarContext.Provider value={{ collapsed, width, hydrated, toggleCollapsed, setCollapsed, setWidth, startDrag }}>
      {children}
    </SidebarContext.Provider>
  );
}
