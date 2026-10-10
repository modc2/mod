"use client";

import {
  createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode,
} from "react";

const DOCKED_KEY = "ct_sidebar_docked";
const WIDTH_KEY = "ct_sidebar_width";
const PANEL_KEY = "ct_sidebar_panel";
const MODE_KEY = "ct_sidebar_mode";
const RECT_KEY = "ct_sidebar_rect";

export const SIDEBAR_MIN = 280;
export const SIDEBAR_MAX = 1600;
export const SIDEBAR_DEFAULT = 420;

export const FLOAT_MIN_W = 320;
export const FLOAT_MIN_H = 220;
/** Gap kept between a popped-out window and the edge of the screen. */
const FLOAT_MARGIN = 16;
/** Drag within this many px of an edge and the window snaps flush to it. */
const SNAP = 20;

/** Which tool the drawer is showing. */
export type SidebarPanel = "watch" | "strat";
/** Docked to the right edge, or popped out as a floating window. */
export type SidebarMode = "dock" | "float";

export type Rect = { x: number; y: number; w: number; h: number };

/**
 * A request to seed the strat builder — with loose traders, or with a whole
 * saved index to load for edit (what the agent's card hands over). The nonce
 * makes a repeat of the same address a fresh event: hitting COPY twice on one
 * row should still re-open the builder.
 */
export type StratSeed = { ss58: string[]; indexId?: string; nonce: number };

interface SidebarContextValue {
  docked: boolean;
  width: number;
  hydrated: boolean;
  panel: SidebarPanel;
  expanded: boolean;
  mode: SidebarMode;
  rect: Rect;
  rolled: boolean;
  stratSeed: StratSeed | null;
  toggleDocked: () => void;
  setDocked: (v: boolean) => void;
  setWidth: (v: number) => void;
  setPanel: (p: SidebarPanel) => void;
  /// Dock the drawer on the strat builder, optionally pre-loading traders.
  openStrat: (...ss58: string[]) => void;
  /// Dock the drawer on the strat builder with a saved index loaded for edit.
  openIndex: (id: string) => void;
  /// Widen the drawer / maximise the window, and back again.
  toggleExpanded: () => void;
  /// Tear the drawer off the edge into a floating window, or put it back.
  setMode: (m: SidebarMode) => void;
  /// Roll the floating window up into its title bar.
  toggleRolled: () => void;
  /// Begin a drag to resize the docked drawer.
  startDrag: (e: React.MouseEvent) => void;
  /// Begin dragging the floating window by its title bar.
  startMove: (e: React.MouseEvent) => void;
  /// Begin resizing the floating window from one of its edges/corners.
  startResize: (e: React.MouseEvent, edge: ResizeEdge) => void;
}

/** Which side(s) of the floating window a resize drag is pulling. */
export type ResizeEdge = "e" | "s" | "se" | "w" | "sw" | "n" | "ne" | "nw";

const SidebarContext = createContext<SidebarContextValue | null>(null);

export function useSidebar() {
  const ctx = useContext(SidebarContext);
  if (!ctx) throw new Error("useSidebar must be used inside <SidebarProvider>");
  return ctx;
}

const DEFAULT_RECT: Rect = { x: 120, y: 120, w: 520, h: 660 };

/** Keep a window on screen — after a pop-out, a monitor change or a reload. */
function clampRect(r: Rect): Rect {
  if (typeof window === "undefined") return r;
  const w = Math.min(Math.max(FLOAT_MIN_W, r.w), window.innerWidth - FLOAT_MARGIN * 2);
  const h = Math.min(Math.max(FLOAT_MIN_H, r.h), window.innerHeight - FLOAT_MARGIN * 2);
  return {
    w, h,
    x: Math.min(Math.max(FLOAT_MARGIN, r.x), Math.max(FLOAT_MARGIN, window.innerWidth - w - FLOAT_MARGIN)),
    y: Math.min(Math.max(FLOAT_MARGIN, r.y), Math.max(FLOAT_MARGIN, window.innerHeight - h - FLOAT_MARGIN)),
  };
}

const save = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch {} };

type Drag =
  | { kind: "dock" }
  | { kind: "move"; dx: number; dy: number }
  | { kind: "resize"; edge: ResizeEdge; start: Rect; mx: number; my: number };

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [docked, setDockedState] = useState(false);
  const [width, setWidthState] = useState(SIDEBAR_DEFAULT);
  const [panel, setPanelState] = useState<SidebarPanel>("watch");
  const [expanded, setExpanded] = useState(false);
  const [mode, setModeState] = useState<SidebarMode>("dock");
  const [rect, setRectState] = useState<Rect>(DEFAULT_RECT);
  const [rolled, setRolled] = useState(false);
  const [stratSeed, setStratSeed] = useState<StratSeed | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const dragRef = useRef<Drag | null>(null);
  // Width to come back to when the drawer un-expands.
  const restoreRef = useRef(SIDEBAR_DEFAULT);
  // Geometry to come back to when the window un-maximises.
  const restoreRectRef = useRef<Rect | null>(null);
  // Live mirrors, so the one global mouse listener never reads a stale value.
  const widthRef = useRef(width);
  const rectRef = useRef(rect);
  widthRef.current = width;
  rectRef.current = rect;

  useEffect(() => {
    try {
      if (localStorage.getItem(DOCKED_KEY) === "1") setDockedState(true);
      const w = Number(localStorage.getItem(WIDTH_KEY));
      if (Number.isFinite(w) && w >= SIDEBAR_MIN && w <= SIDEBAR_MAX) {
        setWidthState(w);
        restoreRef.current = w;
      }
      const p = localStorage.getItem(PANEL_KEY);
      if (p === "watch" || p === "strat") setPanelState(p);
      const m = localStorage.getItem(MODE_KEY);
      if (m === "dock" || m === "float") setModeState(m);
      const raw = localStorage.getItem(RECT_KEY);
      if (raw) {
        const r = JSON.parse(raw);
        if (["x", "y", "w", "h"].every((k) => Number.isFinite(r?.[k]))) setRectState(clampRect(r));
      }
    } catch {}
    setHydrated(true);
  }, []);

  const setDocked = useCallback((v: boolean) => {
    setDockedState(v);
    save(DOCKED_KEY, v ? "1" : "0");
  }, []);

  const toggleDocked = useCallback(() => setDocked(!docked), [docked, setDocked]);

  const setWidth = useCallback((w: number) => {
    const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w));
    setWidthState(clamped);
    save(WIDTH_KEY, String(clamped));
  }, []);

  const setRect = useCallback((r: Rect, persist = true) => {
    const next = clampRect(r);
    setRectState(next);
    if (persist) save(RECT_KEY, JSON.stringify(next));
  }, []);

  const setPanel = useCallback((p: SidebarPanel) => {
    setPanelState(p);
    save(PANEL_KEY, p);
  }, []);

  const setMode = useCallback((m: SidebarMode) => {
    setModeState(m);
    save(MODE_KEY, m);
    setRolled(false);
    setExpanded(false);
    // Pop out at the width you were reading at, parked off the right edge —
    // the window should appear where the drawer was, not in a corner.
    if (m === "float" && typeof window !== "undefined") {
      const w = Math.min(Math.max(FLOAT_MIN_W, widthRef.current), window.innerWidth - FLOAT_MARGIN * 2);
      setRect({
        w,
        h: Math.round(window.innerHeight * 0.78),
        x: window.innerWidth - w - FLOAT_MARGIN * 2,
        y: 96,
      });
    }
    if (m === "dock") setWidth(restoreRef.current);
  }, [setRect, setWidth]);

  const openStrat = useCallback((...ss58: string[]) => {
    setDocked(true);
    setPanel("strat");
    if (ss58.length) setStratSeed((s) => ({ ss58, nonce: (s?.nonce || 0) + 1 }));
  }, [setDocked, setPanel]);

  const openIndex = useCallback((indexId: string) => {
    setDocked(true);
    setPanel("strat");
    setStratSeed((s) => ({ ss58: [], indexId, nonce: (s?.nonce || 0) + 1 }));
  }, [setDocked, setPanel]);

  const toggleRolled = useCallback(() => setRolled((v) => !v), []);

  const toggleExpanded = useCallback(() => {
    if (mode === "float") {
      if (expanded) {
        setRect(restoreRectRef.current || DEFAULT_RECT);
      } else {
        restoreRectRef.current = rectRef.current;
        setRect({
          x: FLOAT_MARGIN, y: FLOAT_MARGIN,
          w: window.innerWidth - FLOAT_MARGIN * 2,
          h: window.innerHeight - FLOAT_MARGIN * 2,
        });
      }
    } else if (expanded) {
      setWidth(restoreRef.current);
    } else {
      restoreRef.current = width;
      // Leave a strip of the page visible — a full-bleed drawer reads as a
      // navigation, and you lose the table you were picking traders off.
      setWidth(Math.max(SIDEBAR_DEFAULT, window.innerWidth - 240));
    }
    setExpanded(!expanded);
  }, [mode, expanded, width, setWidth, setRect]);

  // One global drag listener for all three gestures — dock resize, window
  // move, window resize. It reads the gesture off a ref, so it installs once
  // and never goes stale mid-drag.
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      if (d.kind === "dock") {
        const fromRight = window.innerWidth - e.clientX;
        setWidthState(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, fromRight)));
        return;
      }
      if (d.kind === "move") {
        let x = e.clientX - d.dx;
        let y = e.clientY - d.dy;
        const { w, h } = rectRef.current;
        // Magnetic edges: a window you shove at a side lands flush with it.
        if (Math.abs(x - FLOAT_MARGIN) < SNAP) x = FLOAT_MARGIN;
        if (Math.abs(window.innerWidth - (x + w) - FLOAT_MARGIN) < SNAP) {
          x = window.innerWidth - w - FLOAT_MARGIN;
        }
        if (Math.abs(y - FLOAT_MARGIN) < SNAP) y = FLOAT_MARGIN;
        if (Math.abs(window.innerHeight - (y + h) - FLOAT_MARGIN) < SNAP) {
          y = window.innerHeight - h - FLOAT_MARGIN;
        }
        setRectState(clampRect({ ...rectRef.current, x, y }));
        return;
      }
      const { edge, start, mx, my } = d;
      const dx = e.clientX - mx;
      const dy = e.clientY - my;
      let { x, y, w, h } = start;
      // Each edge moves alone and the opposite one stays pinned — dragging
      // past the screen has to stop the edge, never slide the whole window.
      if (edge.includes("e")) w = Math.min(start.w + dx, window.innerWidth - start.x - FLOAT_MARGIN);
      if (edge.includes("s")) h = Math.min(start.h + dy, window.innerHeight - start.y - FLOAT_MARGIN);
      if (edge.includes("w")) {
        x = Math.max(FLOAT_MARGIN, Math.min(start.x + dx, start.x + start.w - FLOAT_MIN_W));
        w = start.x + start.w - x;
      }
      if (edge.includes("n")) {
        y = Math.max(FLOAT_MARGIN, Math.min(start.y + dy, start.y + start.h - FLOAT_MIN_H));
        h = start.y + start.h - y;
      }
      setRectState(clampRect({ x, y, w, h }));
    };
    const onUp = () => {
      const d = dragRef.current;
      if (!d) return;
      dragRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (d.kind === "dock") {
        // A hand-dragged width is the one to come back to, and it ends the
        // expanded state — the button would otherwise snap away your drag.
        restoreRef.current = widthRef.current;
        setExpanded(false);
        save(WIDTH_KEY, String(widthRef.current));
      } else {
        if (d.kind === "resize") setExpanded(false);
        save(RECT_KEY, JSON.stringify(rectRef.current));
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // A shrinking viewport must not strand the window off-screen.
  useEffect(() => {
    const onResize = () => setRectState((r) => clampRect(r));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const begin = (e: React.MouseEvent, d: Drag, cursor: string) => {
    e.preventDefault();
    dragRef.current = d;
    document.body.style.cursor = cursor;
    document.body.style.userSelect = "none";
  };

  const startDrag = useCallback((e: React.MouseEvent) => {
    begin(e, { kind: "dock" }, "col-resize");
  }, []);

  const startMove = useCallback((e: React.MouseEvent) => {
    begin(e, { kind: "move", dx: e.clientX - rectRef.current.x, dy: e.clientY - rectRef.current.y }, "grabbing");
  }, []);

  const startResize = useCallback((e: React.MouseEvent, edge: ResizeEdge) => {
    e.stopPropagation();
    begin(e, { kind: "resize", edge, start: rectRef.current, mx: e.clientX, my: e.clientY }, `${edge}-resize`);
  }, []);

  return (
    <SidebarContext.Provider
      value={{
        docked, width, hydrated, panel, expanded, mode, rect, rolled, stratSeed,
        toggleDocked, setDocked, setWidth, setPanel, openStrat, openIndex,
        toggleExpanded, setMode, toggleRolled, startDrag, startMove, startResize,
      }}
    >
      {children}
    </SidebarContext.Provider>
  );
}
