"use client";

// AgentShell — lifts HelpAgent's open/closed state to layout level so the
// agent is reachable from every page, including the AccessGate.
//
// Previously the state lived in TopBar and HelpAgent only rendered on
// authenticated pages. Moving it here means:
//   - gate page → AGENT button in AccessGate dispatches TOGGLE_AGENT_EVENT
//   - authenticated pages → NavMenu mark dispatches the same event
// Both arrive here; one handler, one source of truth.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import HelpAgent, { OPEN_AGENT_EVENT } from "./HelpAgent";

/** Dispatch to toggle the agent open/closed from anywhere. */
export const TOGGLE_AGENT_EVENT = "poly-toggle-agent";
/** Dispatched by AgentShell on every state change — lets NavMenu sync its ring indicator. */
export const AGENT_STATE_EVENT = "poly-agent-state";

const AGENT_KEY = "poly_agent_sidebar";
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function AgentShell() {
  const [open, setOpen] = useState(false);
  // Ref so the toggle event handler always sees the current value without
  // re-registering on every render.
  const openRef = useRef(false);

  useIsoLayoutEffect(() => {
    try {
      const stored = localStorage.getItem(AGENT_KEY) === "1";
      openRef.current = stored;
      setOpen(stored);
    } catch {}
  }, []);

  const setAgent = useCallback((next: boolean) => {
    openRef.current = next;
    setOpen(next);
    try { localStorage.setItem(AGENT_KEY, next ? "1" : "0"); } catch {}
    window.dispatchEvent(new CustomEvent(AGENT_STATE_EVENT, { detail: { open: next } }));
  }, []);

  useEffect(() => {
    const onOpen = () => setAgent(true);
    const onToggle = () => setAgent(!openRef.current);
    window.addEventListener(OPEN_AGENT_EVENT, onOpen);
    window.addEventListener(TOGGLE_AGENT_EVENT, onToggle);
    return () => {
      window.removeEventListener(OPEN_AGENT_EVENT, onOpen);
      window.removeEventListener(TOGGLE_AGENT_EVENT, onToggle);
    };
  }, [setAgent]);

  return <HelpAgent open={open} onClose={() => setAgent(false)} />;
}
