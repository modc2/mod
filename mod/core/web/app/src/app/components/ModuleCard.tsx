"use client";

import Link from "next/link";
import { useCallback } from "react";
import { gatewayUrl, type Module } from "@/lib/api";
import { bloc } from "@/lib/chain";
import { LivePreview } from "./LivePreview";
import { Icon } from "./Icon";

// Deterministic accent for modules that don't declare a color — hash the name
// into a pleasant hue so the grid stays colorful without being random per-render.
function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

// One card per module. Reads top-down the way a visitor scans it: what it
// looks like (live preview), what it's called, what it does, then one clear
// action. Developer facts (owner, CID, ports) live on the detail page.
export function ModuleCard({
  m,
  index,
  staked = 0,
}: {
  m: Module;
  index: number;
  /** BlocTime (wei) staked to this module — shown as a badge when > 0. */
  staked?: number;
}) {
  const glow = m.color || hueFromName(m.name);
  const label = (m.icon || m.name[0] || "m").slice(0, 1);
  const depCount = m.deps?.length ?? 0;
  const openable = m.has_app && m.on_web;

  // Feed the cursor position to the CSS spotlight (.card::after).
  const track = useCallback((e: React.MouseEvent<HTMLAnchorElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, []);

  return (
    <Link
      href={`/mods/${m.name}`}
      className={`card${openable ? " openable" : ""}`}
      onMouseMove={track}
      style={
        {
          "--glow": glow,
          animationDelay: `${Math.min(index, 24) * 28}ms`,
        } as React.CSSProperties
      }
    >
      {m.has_app && (
        <div className="card-media">
          <LivePreview url={gatewayUrl(m.name)} label={label} glow={glow} />
        </div>
      )}
      <div className="card-top">
        <span className="avatar" style={{ background: glow }}>
          {label}
        </span>
        <div className="card-title-wrap">
          <div className="title">{m.name}</div>
          <div className="ver">v{m.version}</div>
        </div>
        {m.on_web && (
          <span className="onweb" title={`Running now at ${gatewayUrl(m.name)}`}>
            <i className="live-dot" />
            Live
          </span>
        )}
        {m.registered && (
          <span className="onchain" title="Registered in the on-chain Registry">
            <Icon name="chain" />
            On-chain
          </span>
        )}
      </div>
      <div className="desc">{m.description || "No description yet."}</div>
      <div className="card-foot">
        <div className="card-tags">
          {staked > 0 && (
            <span className="tag bloc" title="BlocTime staked to this module">
              <Icon name="clock" /> {bloc(staked)} BLOC
            </span>
          )}
          {m.has_app && <span className="tag app">app</span>}
          {m.fn_count > 0 && (
            <span className="tag fns" title="Functions you can call over its API">
              {m.fn_count} function{m.fn_count === 1 ? "" : "s"}
            </span>
          )}
          {m.has_rust && <span className="tag rust">rust</span>}
          {depCount > 0 && (
            <span className="tag deps" title={`Uses: ${m.deps.join(", ")}`}>
              uses {depCount}
            </span>
          )}
        </div>
        <span className="card-open">
          {openable ? "Open" : "Details"}
          <Icon name="arrow-right" />
        </span>
      </div>
    </Link>
  );
}
