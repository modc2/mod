"use client";

import Link from "next/link";
import { useCallback } from "react";
import { gatewayUrl, type Module } from "@/lib/api";
import { LivePreview } from "./LivePreview";

// Deterministic accent for modules that don't declare a color — hash the name
// into a pleasant hue so the grid stays colorful without being random per-render.
function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

// Middle-truncate long identifiers (addresses, CIDs) so they fit one line but
// stay recognizable by their head/tail — the full value rides along in `title`.
function shorten(s: string, head = 6, tail = 4): string {
  return s.length > head + tail + 1 ? `${s.slice(0, head)}…${s.slice(-tail)}` : s;
}

export function ModuleCard({ m, index }: { m: Module; index: number }) {
  const glow = m.color || hueFromName(m.name);
  const label = (m.icon || m.name[0] || "m").slice(0, 1);
  const depCount = m.deps?.length ?? 0;
  const owner =
    typeof m.config?.owner === "string" && m.config.owner ? (m.config.owner as string) : null;
  const cid = m.schema || null;

  // Feed the cursor position to the CSS spotlight (.card::after).
  const track = useCallback((e: React.MouseEvent<HTMLAnchorElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, []);

  return (
    <Link
      href={`/mods/${m.name}`}
      className="card"
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
        <div>
          <div className="title">{m.name}</div>
          <div className="ver">v{m.version}</div>
        </div>
        {m.on_web && (
          <span className="onweb" title={`Live on the web at ${gatewayUrl(m.name)}`}>
            ● live
          </span>
        )}
        {m.registered && (
          <span className="onchain" title="Registered in the on-chain Registry">
            ⛓ on-chain
          </span>
        )}
      </div>
      <div className="desc">{m.description || "No description provided."}</div>
      {(owner || cid) && (
        <div className="card-meta">
          {owner && (
            <span className="meta-row" title={`Owner: ${owner}`}>
              <span className="meta-k">owner</span>
              <span className="meta-v">{shorten(owner)}</span>
            </span>
          )}
          {cid && (
            <span className="meta-row" title={`CID: ${cid}`}>
              <span className="meta-k">cid</span>
              <span className="meta-v">{shorten(cid)}</span>
            </span>
          )}
        </div>
      )}
      <div className="card-foot">
        {m.has_rust && <span className="tag rust">rust</span>}
        {m.has_app && <span className="tag app">app</span>}
        {m.fn_count > 0 && <span className="tag fns">{m.fn_count} fns</span>}
        {depCount > 0 && (
          <span className="tag deps" title={`Depends on: ${m.deps.join(", ")}`}>
            ↳ {depCount} dep{depCount > 1 ? "s" : ""}
          </span>
        )}
        {m.port && <span className="tag">:{m.port}</span>}
      </div>
      <span className="card-go" aria-hidden>
        →
      </span>
    </Link>
  );
}
