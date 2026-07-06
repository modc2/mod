"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type { Graph, GraphNode } from "@/lib/api";

// Deterministic accent for nodes without a declared color — matches the card grid.
function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

type Placed = GraphNode & {
  x: number;
  y: number;
  inDeg: number;
  outDeg: number;
};

const VW = 880;
const VH = 600;
const CX = VW / 2;
const CY = VH / 2;

// Lay every connected node onto one of two rings: modules that are depended
// upon (the protocol's hubs — chain, store, …) sit on an inner ring, pure
// consumers fan out on an outer ring. The split makes the "who depends on whom"
// shape legible without a physics simulation.
function layout(graph: Graph): { placed: Placed[]; byName: Map<string, Placed> } {
  const inDeg = new Map<string, number>();
  const outDeg = new Map<string, number>();
  for (const n of graph.nodes) {
    inDeg.set(n.name, 0);
    outDeg.set(n.name, 0);
  }
  for (const e of graph.edges) {
    inDeg.set(e.to, (inDeg.get(e.to) ?? 0) + 1);
    outDeg.set(e.from, (outDeg.get(e.from) ?? 0) + 1);
  }

  const connected = graph.nodes.filter(
    (n) => (inDeg.get(n.name) ?? 0) + (outDeg.get(n.name) ?? 0) > 0,
  );
  const hubs = connected.filter((n) => (inDeg.get(n.name) ?? 0) > 0);
  const leaves = connected.filter((n) => (inDeg.get(n.name) ?? 0) === 0);

  const place = (list: GraphNode[], radius: number, phase: number): Placed[] =>
    list.map((n, i) => {
      // A lone hub sits dead center; otherwise spread evenly around the ring.
      const single = list.length === 1 && radius < 160;
      const a = phase + (i / Math.max(list.length, 1)) * Math.PI * 2;
      return {
        ...n,
        inDeg: inDeg.get(n.name) ?? 0,
        outDeg: outDeg.get(n.name) ?? 0,
        x: single ? CX : CX + Math.cos(a) * radius,
        y: single ? CY : CY + Math.sin(a) * radius,
      };
    });

  const placed = [
    ...place(hubs, hubs.length === 1 ? 0 : 150, -Math.PI / 2),
    ...place(leaves, 250, -Math.PI / 2 + 0.3),
  ];
  const byName = new Map(placed.map((p) => [p.name, p]));
  return { placed, byName };
}

export default function DepGraph({ graph }: { graph: Graph }) {
  const router = useRouter();
  const [hover, setHover] = useState<string | null>(null);
  const { placed, byName } = useMemo(() => layout(graph), [graph]);

  if (placed.length === 0) {
    return (
      <div className="empty">
        no dependency links yet — modules declare deps in their config.json
      </div>
    );
  }

  // Which edges/nodes to emphasize given the hovered node.
  const isLit = (name: string) => {
    if (!hover) return true;
    if (name === hover) return true;
    return graph.edges.some(
      (e) =>
        (e.from === hover && e.to === name) ||
        (e.to === hover && e.from === name),
    );
  };
  const edgeLit = (from: string, to: string) =>
    !hover || from === hover || to === hover;

  return (
    <div className="depgraph">
      <svg
        viewBox={`0 0 ${VW} ${VH}`}
        className="depgraph-svg"
        role="img"
        aria-label="Module dependency graph"
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="rgba(124,139,255,0.8)" />
          </marker>
        </defs>

        {graph.edges.map((e, i) => {
          const a = byName.get(e.from);
          const b = byName.get(e.to);
          if (!a || !b) return null;
          // Stop the line short of the target node so the arrowhead is visible.
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const len = Math.hypot(dx, dy) || 1;
          const r = 26;
          const ex = b.x - (dx / len) * r;
          const ey = b.y - (dy / len) * r;
          const lit = edgeLit(e.from, e.to);
          return (
            <line
              key={i}
              x1={a.x}
              y1={a.y}
              x2={ex}
              y2={ey}
              className="depedge"
              markerEnd="url(#arrow)"
              style={{ opacity: lit ? 0.55 : 0.08 }}
            />
          );
        })}

        {placed.map((n) => {
          const color = n.color || hueFromName(n.name);
          const lit = isLit(n.name);
          const radius = 18 + Math.min(n.inDeg, 6) * 2.5;
          return (
            <g
              key={n.name}
              className="depnode"
              transform={`translate(${n.x},${n.y})`}
              style={{ opacity: lit ? 1 : 0.22, cursor: "pointer" }}
              onMouseEnter={() => setHover(n.name)}
              onMouseLeave={() => setHover(null)}
              onClick={() => router.push(`/mods/${n.name}`)}
            >
              {n.registered && (
                <circle r={radius + 4} className="depnode-ring" />
              )}
              <circle r={radius} fill={color} className="depnode-dot" />
              <text className="depnode-glyph" dy="0.35em">
                {(n.icon || n.name[0] || "m").slice(0, 1).toUpperCase()}
              </text>
              <text className="depnode-label" y={radius + 15}>
                {n.name}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="depgraph-legend">
        <span>
          <i className="lg-dot" /> module
        </span>
        <span>
          <i className="lg-ring" /> on-chain
        </span>
        <span>
          <i className="lg-arrow">→</i> depends on
        </span>
        <span className="lg-hint">bigger = more depended upon · click to open</span>
      </div>
    </div>
  );
}
