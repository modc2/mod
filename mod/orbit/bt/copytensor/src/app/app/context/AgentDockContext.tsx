"use client";

import {
  createContext, useCallback, useContext, useEffect, useState, ReactNode,
} from "react";
import { fetchPendingApprovals } from "../lib/api";

/**
 * The desk agent as a window you summon from any page, not a tab you leave
 * for. Three facts are shared between the top-bar button and the dock:
 * whether it's open, whether the agent is mid-answer, and how many writes
 * are parked waiting on you — the last one is the reason this is a context
 * at all: a write sitting in the queue must be visible from every page,
 * because the agent is blocked on it until you answer.
 */
interface AgentDockValue {
  open: boolean;
  /** Mounted at least once — the dock stays mounted after that so a
      conversation keeps streaming while the window is shut. */
  armed: boolean;
  busy: boolean;
  pending: number;
  setOpen: (v: boolean) => void;
  toggle: () => void;
  setBusy: (v: boolean) => void;
  refreshPending: () => void;
}

const Ctx = createContext<AgentDockValue | null>(null);

// The queue is a tiny local read; 15 s is fast enough that a parked write
// lights the button well inside its 10-minute TTL.
const POLL_MS = 15_000;

export function AgentDockProvider({ children }: { children: ReactNode }) {
  const [open, setOpenRaw] = useState(false);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(0);

  const setOpen = useCallback((v: boolean) => {
    if (v) setArmed(true);
    setOpenRaw(v);
  }, []);
  const toggle = useCallback(() => {
    setArmed(true);
    setOpenRaw((o) => !o);
  }, []);

  const refreshPending = useCallback(() => {
    fetchPendingApprovals()
      .then((r) => setPending(r.pending.length))
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshPending();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") refreshPending();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [refreshPending]);

  return (
    <Ctx.Provider value={{ open, armed, busy, pending, setOpen, toggle, setBusy, refreshPending }}>
      {children}
    </Ctx.Provider>
  );
}

/** Null outside the provider, so StratAgent still works standalone. */
export const useAgentDock = () => useContext(Ctx);
