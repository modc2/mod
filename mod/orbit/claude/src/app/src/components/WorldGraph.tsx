"use client";

// WorldGraph — the HUB's "graph" layout: the whole fleet in one picture.
//
// Every module is a node; the two things that connect modules are drawn as
// two kinds of edge:
//
//   · DEPENDENCY (solid, arrowed) — what a module's config.json declares it
//     needs. Points from the dependent down to what it depends on.
//   · FORK (dashed) — where a module came from. The API recovers this three
//     ways: the record a fork writes about itself, an exact shared tree CID,
//     and — for every fork made before anyone wrote it down — overlap of file
//     blobs with a tree somebody else had first. Inferred edges are drawn
//     fainter than stated ones, because they are inferred.
//
// A node's size is how much it has CHANGED (versions in its log), so the
// modules actually being worked on read as the big ones, and its ring is lit
// when the module is running. Layout is a small deterministic force
// simulation — seeded from the node name, never Math.random, so the map looks
// the same every time you open it and you can learn where things live.
//
// Everything is hand-drawn SVG on the same CSS variables as the rest of the
// console, so it themes light/dark for free.

import { useEffect, useMemo, useRef, useState } from "react";

export type WorldNode = {
  name: string;
  category: string;
  path?: string | null;
  description?: string | null;
  version?: string | null;
  exists: boolean;
  changes: number;
  edits: number;
  snapshots: number;
  restores: number;
  authors: number;
  first_change?: number | null;
  last_change?: number | null;
  head_cid?: string | null;
};

export type WorldEdge = {
  from: string;
  to: string;
  kind: "dep" | "fork";
  via: string;
  weight?: number | null;
};

export type World = {
  generated_at: number;
  nodes: WorldNode[];
  edges: WorldEdge[];
  stats: { modules: number; with_history: number; changes: number; dep_edges: number; fork_edges: number };
};

const W = 1100;
const H = 720;
const ITERATIONS = 320;

/** Stable hash → the seeded jitter that stands in for random placement. */
function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

type Sim = { name: string; x: number; y: number; vx: number; vy: number; r: number; deg: number };

/** Node radius: sublinear in change count so a 100-version module doesn't
 *  swallow the map, with a floor that keeps never-touched modules clickable. */
function radiusFor(changes: number): number {
  return 5 + Math.sqrt(changes) * 2.4;
}

/**
 * Force-directed layout: springs along edges, all-pairs repulsion, and a weak
 * pull to centre so disconnected modules don't drift off the canvas. Runs once
 * per graph in a useMemo — a few hundred iterations over ~280 nodes is a few
 * milliseconds, so there's no animation loop to babysit.
 */
function layout(nodes: WorldNode[], edges: WorldEdge[]): Map<string, Sim> {
  const sims = new Map<string, Sim>();
  const present = new Set(nodes.map((n) => n.name));
  const deg = new Map<string, number>();
  for (const e of edges) {
    if (!present.has(e.from) || !present.has(e.to)) continue;
    deg.set(e.from, (deg.get(e.from) || 0) + 1);
    deg.set(e.to, (deg.get(e.to) || 0) + 1);
  }
  // Connected modules seed near the centre, isolated ones on a ring around the
  // outside — so the wiring is legible instead of buried under 200 loners.
  const loners = nodes.filter((n) => !deg.get(n.name));
  let li = 0;
  for (const n of nodes) {
    const connected = !!deg.get(n.name);
    let x: number;
    let y: number;
    if (connected) {
      x = W / 2 + (hash(n.name) - 0.5) * W * 0.45;
      y = H / 2 + (hash(n.name + "y") - 0.5) * H * 0.45;
    } else {
      const t = (li++ / Math.max(1, loners.length)) * Math.PI * 2;
      x = W / 2 + Math.cos(t) * W * 0.44;
      y = H / 2 + Math.sin(t) * H * 0.44;
    }
    sims.set(n.name, { name: n.name, x, y, vx: 0, vy: 0, r: radiusFor(n.changes), deg: deg.get(n.name) || 0 });
  }

  const live = edges.filter((e) => present.has(e.from) && present.has(e.to));
  const arr = [...sims.values()];
  for (let step = 0; step < ITERATIONS; step++) {
    const cool = 1 - step / ITERATIONS;
    // Repulsion — every pair, capped by distance so far-apart nodes are free.
    for (let i = 0; i < arr.length; i++) {
      for (let j = i + 1; j < arr.length; j++) {
        const a = arr[i];
        const b = arr[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 > 90000) continue;
        if (d2 < 1) {
          dx = (hash(a.name + b.name) - 0.5) || 0.5;
          dy = (hash(b.name + a.name) - 0.5) || 0.5;
          d2 = 1;
        }
        const d = Math.sqrt(d2);
        const push = ((a.r + b.r + 26) * (a.r + b.r + 26)) / d2 / d;
        a.vx -= dx * push;
        a.vy -= dy * push;
        b.vx += dx * push;
        b.vy += dy * push;
      }
    }
    // Springs — dependency edges pull tighter than fork edges so a family
    // stays a family without collapsing onto its parent.
    for (const e of live) {
      const a = sims.get(e.from)!;
      const b = sims.get(e.to)!;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1;
      const rest = e.kind === "dep" ? 120 : 90;
      const k = (e.kind === "dep" ? 0.02 : 0.03) * (d - rest);
      a.vx += (dx / d) * k;
      a.vy += (dy / d) * k;
      b.vx -= (dx / d) * k;
      b.vy -= (dy / d) * k;
    }
    for (const n of arr) {
      n.vx += (W / 2 - n.x) * 0.0016;
      n.vy += (H / 2 - n.y) * 0.0016;
      n.x += n.vx * cool * 0.4;
      n.y += n.vy * cool * 0.4;
      n.vx *= 0.82;
      n.vy *= 0.82;
      n.x = Math.max(n.r + 6, Math.min(W - n.r - 6, n.x));
      n.y = Math.max(n.r + 6, Math.min(H - n.r - 6, n.y));
    }
  }
  return sims;
}

const CATEGORY_COLOR: Record<string, string> = {
  orbit: "var(--accent-color)",
  core: "var(--accent-color-2)",
  portal: "var(--crt-blue)",
  gone: "var(--text-tertiary)",
};

function timeAgo(ts?: number | null): string | null {
  if (!ts) return null;
  const d = Math.floor(Date.now() / 1000) - ts;
  if (d < 3600) return `${Math.max(1, Math.floor(d / 60))}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function WorldGraph({
  world,
  loading,
  error,
  liveOf,
  selected,
  onOpen,
  onRefresh,
}: {
  world: World | null;
  loading: boolean;
  error: string | null;
  liveOf: (name: string) => boolean | null;
  selected?: string | null;
  onOpen: (name: string) => void;
  onRefresh?: () => void;
}) {
  // Which edge kinds to draw. Dependencies and forks answer different
  // questions, and with both on a busy fleet the picture gets noisy.
  const [showDeps, setShowDeps] = useState(true);
  const [showForks, setShowForks] = useState(true);
  const [hover, setHover] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const wrapRef = useRef<HTMLDivElement>(null);

  const nodes = world?.nodes ?? [];
  const edges = useMemo(
    () => (world?.edges ?? []).filter((e) => (e.kind === "dep" ? showDeps : showForks)),
    [world, showDeps, showForks],
  );
  // Layout uses ALL edges regardless of the filters — toggling what you see
  // shouldn't rearrange the map under you.
  const pos = useMemo(() => layout(nodes, world?.edges ?? []), [nodes, world]);
  const byName = useMemo(() => new Map(nodes.map((n) => [n.name, n] as const)), [nodes]);

  // Hovering a node dims everything it isn't wired to.
  const related = useMemo(() => {
    if (!hover) return null;
    const set = new Set<string>([hover]);
    for (const e of edges) {
      if (e.from === hover) set.add(e.to);
      if (e.to === hover) set.add(e.from);
    }
    return set;
  }, [hover, edges]);

  useEffect(() => {
    const el = wrapRef.current;
    if (el) el.scrollTo({ left: (el.scrollWidth - el.clientWidth) / 2, top: (el.scrollHeight - el.clientHeight) / 2 });
  }, [world]);

  const hovered = hover ? byName.get(hover) : null;
  const hoveredEdges = hover
    ? (world?.edges ?? []).filter((e) => e.from === hover || e.to === hover)
    : [];

  if (error) return <div className="wgr-note err">✗ {error}</div>;
  if (loading && !world) return <div className="wgr-note">mapping the fleet…</div>;
  if (!world) return null;

  return (
    <div className="wgr">
      <div className="wgr-bar">
        <span className="wgr-stat">
          <b>{world.stats.modules}</b> modules
        </span>
        <span className="wgr-stat">
          <b>{world.stats.changes}</b> changes across <b>{world.stats.with_history}</b>
        </span>
        <button
          className={showDeps ? "wgr-pill on" : "wgr-pill"}
          onClick={() => setShowDeps((v) => !v)}
          title="Edges from a module's config.json deps — what it needs to run."
        >
          — dependencies ({world.stats.dep_edges})
        </button>
        <button
          className={showForks ? "wgr-pill on" : "wgr-pill"}
          onClick={() => setShowForks((v) => !v)}
          title="Where a module came from: a fork record, an identical tree CID, or an overlap of file blobs with a tree somebody had first."
        >
          ⋯ forks ({world.stats.fork_edges})
        </button>
        <span className="wgr-zoom">
          {([0.7, 1, 1.4] as const).map((z) => (
            <button key={z} className={zoom === z ? "on" : undefined} onClick={() => setZoom(z)}>
              {z === 0.7 ? "−" : z === 1 ? "1:1" : "+"}
            </button>
          ))}
        </span>
        {onRefresh && (
          <button className="wgr-pill" onClick={onRefresh} title="Rebuild the graph">
            ↻
          </button>
        )}
      </div>

      <div className="wgr-canvas" ref={wrapRef}>
        <svg width={W * zoom} height={H * zoom} viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
          <defs>
            <marker id="wgr-arrow" markerWidth="7" markerHeight="7" refX="6.5" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" fill="color-mix(in srgb, var(--crt-green) 60%, transparent)" />
            </marker>
          </defs>

          {edges.map((e, i) => {
            const a = pos.get(e.from);
            const b = pos.get(e.to);
            if (!a || !b) return null;
            const dim = related ? !(related.has(e.from) && related.has(e.to)) : false;
            const isDep = e.kind === "dep";
            // Stop the line at the target's rim so the arrow reads cleanly.
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const d = Math.hypot(dx, dy) || 1;
            const x2 = b.x - (dx / d) * (b.r + 4);
            const y2 = b.y - (dy / d) * (b.r + 4);
            const stated = e.via !== "blob-overlap";
            return (
              <line
                key={i}
                x1={a.x}
                y1={a.y}
                x2={x2}
                y2={y2}
                stroke={
                  isDep
                    ? "color-mix(in srgb, var(--crt-green) 45%, transparent)"
                    : "color-mix(in srgb, var(--crt-blue) 55%, transparent)"
                }
                strokeWidth={isDep ? 1.4 : 1.2}
                strokeDasharray={isDep ? undefined : stated ? "5 3" : "2 4"}
                markerEnd={isDep ? "url(#wgr-arrow)" : undefined}
                opacity={dim ? 0.08 : stated ? 0.9 : 0.6}
              >
                <title>
                  {isDep
                    ? `${e.from} depends on ${e.to}`
                    : `${e.from} forked from ${e.to} · ${e.via}${e.weight != null && e.via === "blob-overlap" ? ` (${Math.round(e.weight * 100)}% shared blobs)` : ""}`}
                </title>
              </line>
            );
          })}

          {nodes.map((n) => {
            const p = pos.get(n.name);
            if (!p) return null;
            const live = liveOf(n.name);
            const dim = related ? !related.has(n.name) : false;
            const isSel = n.name === selected;
            const color = CATEGORY_COLOR[n.category] || CATEGORY_COLOR.gone;
            // Labels for everything would be a wall of text — name the ones
            // that carry weight, plus whatever you're pointing at.
            const labelled = n.changes >= 3 || p.deg > 0 || isSel || hover === n.name;
            return (
              <g
                key={n.name}
                transform={`translate(${p.x},${p.y})`}
                opacity={dim ? 0.15 : 1}
                style={{ cursor: n.exists ? "pointer" : "not-allowed" }}
                onMouseEnter={() => setHover(n.name)}
                onMouseLeave={() => setHover((h) => (h === n.name ? null : h))}
                onClick={() => n.exists && onOpen(n.name)}
              >
                {isSel && <circle r={p.r + 6} fill="none" stroke={color} strokeWidth={1.2} opacity={0.8} />}
                <circle
                  r={p.r}
                  fill={`color-mix(in srgb, ${color} ${n.exists ? 28 : 10}%, transparent)`}
                  stroke={live === true ? "var(--crt-green)" : color}
                  strokeWidth={live === true ? 1.8 : 1}
                  strokeDasharray={n.exists ? undefined : "3 3"}
                />
                {live === true && <circle r={2} fill="var(--crt-green)" />}
                {labelled && (
                  <text
                    className="wgr-label"
                    y={p.r + 11}
                    textAnchor="middle"
                    fill={hover === n.name || isSel ? "var(--text-primary)" : "var(--text-tertiary)"}
                  >
                    {n.name}
                  </text>
                )}
                <title>
                  {[
                    n.name,
                    n.version ? `v${n.version}` : null,
                    `${n.changes} change${n.changes === 1 ? "" : "s"}`,
                    n.authors > 1 ? `${n.authors} authors` : null,
                    timeAgo(n.last_change) ? `last ${timeAgo(n.last_change)}` : null,
                    n.exists ? null : "no directory — history only",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </title>
              </g>
            );
          })}
        </svg>
      </div>

      {hovered && (
        <div className="wgr-card">
          <div className="wgr-card-head">
            <span className="wgr-card-name">{hovered.name}</span>
            {hovered.version && <span className="wgr-card-ver">v{hovered.version}</span>}
            <span className="wgr-card-cat">{hovered.category}</span>
          </div>
          {hovered.description && <p className="wgr-card-desc">{hovered.description}</p>}
          <div className="wgr-card-stats">
            <span><b>{hovered.changes}</b> changes</span>
            <span><b>{hovered.edits}</b> agent edits</span>
            {hovered.restores > 0 && <span><b>{hovered.restores}</b> rollbacks</span>}
            {hovered.authors > 0 && <span><b>{hovered.authors}</b> author{hovered.authors === 1 ? "" : "s"}</span>}
            {timeAgo(hovered.last_change) && <span>last {timeAgo(hovered.last_change)}</span>}
          </div>
          {hoveredEdges.length > 0 && (
            <div className="wgr-card-edges">
              {hoveredEdges.slice(0, 6).map((e, i) => (
                <span key={i} className={e.kind === "dep" ? "dep" : "fork"}>
                  {e.kind === "dep"
                    ? e.from === hovered.name
                      ? `needs ${e.to}`
                      : `used by ${e.from}`
                    : e.from === hovered.name
                      ? `forked from ${e.to}`
                      : `forked into ${e.from}`}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
