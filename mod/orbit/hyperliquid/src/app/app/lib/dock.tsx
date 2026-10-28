"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

// The desk popout: the agent, the strats you manage and your wallet, docked
// to the right of every page. State lives here because the toggle (header)
// and the panel (body) are two components apart, and anything on any page
// can open a given tab by dispatching OPEN_DOCK_EVENT.
export type DockTab = "agent" | "strats" | "wallet";
export const DOCK_TABS: { key: DockTab; label: string }[] = [
  { key: "agent", label: "Agent" },
  { key: "strats", label: "Strats" },
  { key: "wallet", label: "Wallet" },
];

export const OPEN_DOCK_EVENT = "hl:dock";
/** Open the desk (optionally on a tab) from anywhere — no context needed. */
export const openDock = (tab?: DockTab) =>
  window.dispatchEvent(new CustomEvent(OPEN_DOCK_EVENT, { detail: tab }));

const KEY = "hl_dock";

type DockState = {
  open: boolean;
  tab: DockTab;
  show: (tab?: DockTab) => void;
  close: () => void;
  toggle: (tab?: DockTab) => void;
  setTab: (tab: DockTab) => void;
};

const C = createContext<DockState>({
  open: false, tab: "agent",
  show: () => {}, close: () => {}, toggle: () => {}, setTab: () => {},
});

export function DockProvider({ children }: { children: React.ReactNode }) {
  // Default CLOSED: the desk is a second surface, not the page.
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<DockTab>("agent");

  // `ready` gates the writer below: without it React's double-invoked mount
  // effects persist the defaults on top of the state just restored, and the
  // desk forgets which tab you left it on.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || "{}");
      if (v?.tab && DOCK_TABS.some((t) => t.key === v.tab)) setTab(v.tab);
      setOpen(!!v?.open);
    } catch {}
    setReady(true);
  }, []);

  // `data-dock` is what reserves the column at wide widths (globals.css).
  useEffect(() => {
    document.documentElement.dataset.dock = open ? "open" : "closed";
    if (!ready) return;
    try { localStorage.setItem(KEY, JSON.stringify({ open, tab })); } catch {}
  }, [open, tab, ready]);

  const show = useCallback((t?: DockTab) => { if (t) setTab(t); setOpen(true); }, []);
  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback((t?: DockTab) => {
    setOpen((o) => {
      // Clicking a tab's own handle while it's showing closes; clicking a
      // different tab switches to it rather than shutting the panel.
      if (o && t && t !== tab) { setTab(t); return true; }
      if (t) setTab(t);
      return !o;
    });
  }, [tab]);

  useEffect(() => {
    const onOpen = (e: Event) => show((e as CustomEvent).detail as DockTab | undefined);
    window.addEventListener(OPEN_DOCK_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_DOCK_EVENT, onOpen);
  }, [show]);

  const value = useMemo(
    () => ({ open, tab, show, close, toggle, setTab }),
    [open, tab, show, close, toggle],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}

export const useDock = () => useContext(C);
