"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAgentDock } from "../context/AgentDockContext";
import AgentBot from "./AgentBot";
import StratAgent from "./StratAgent";

/**
 * The agent window. Floats over the right of whatever page you're on, so
 * you can ask "who's this trader?" while looking at them.
 *
 * Mounted on first open and never again unmounted (only hidden): the reply
 * is a live stream, and a parked write is answered from this transcript —
 * shutting the window must not cut either off. The one exception is the
 * full-page /agent route, which is the same console at full size; two live
 * copies of one transcript would fight over its localStorage slot.
 */
export default function AgentDock() {
  const dock = useAgentDock();
  const path = usePathname() || "";
  const onAgentPage = path === "/agent" || path.startsWith("/agent/");
  const open = !!dock?.open && !onAgentPage;

  useEffect(() => {
    if (!open || !dock) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") dock.setOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, dock]);

  useEffect(() => {
    if (onAgentPage) dock?.setOpen(false);
  }, [onAgentPage]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!dock?.armed || onAgentPage) return null;

  return (
    <>
      {/* Phone only: the window is the whole screen, so the scrim is what
          you tap to put it away. */}
      <div
        className={`agent-dock-scrim ${open ? "" : "agent-dock--shut"}`}
        onClick={() => dock.setOpen(false)}
        aria-hidden="true"
      />
      <aside
        className={`agent-dock ${open ? "" : "agent-dock--shut"}`}
        role="dialog"
        aria-label="Desk agent"
        aria-hidden={!open}
      >
        <header className="agent-dock__head">
          <span className="agent-dock__face"><AgentBot size={22} /></span>
          <div className="min-w-0 flex-1">
            <p className="agent-dock__title">DESK AGENT</p>
            <p className="agent-dock__sub">reads anything · asks before it trades</p>
          </div>
          <Link
            href="/agent"
            className="pixel-btn agent-dock__ctl no-underline"
            title="Open the agent full page"
          >
            ⤢
          </Link>
          <button
            className="pixel-btn agent-dock__ctl"
            onClick={() => dock.setOpen(false)}
            aria-label="Close agent"
            title="Close (Esc)"
          >
            ✕
          </button>
        </header>
        <div className="agent-dock__body">
          <StratAgent compact onLeave={() => dock.setOpen(false)} />
        </div>
      </aside>
    </>
  );
}
