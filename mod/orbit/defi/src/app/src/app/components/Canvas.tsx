"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { BlockSpec, Catalog, Graph, GraphNode, Report } from "../lib/types";

export const NODE_W = 236;
const HEAD_H = 40;
const ROW_H = 22;
const BODY_PAD = 7;

export function nodeHeight(spec: BlockSpec | undefined): number {
  const rows = (spec?.inputs.length ?? 0) + 1; // inputs + the provides row
  return HEAD_H + BODY_PAD + rows * ROW_H + 9;
}

/// Where a port sits in graph coordinates. Everything (wires, drag previews,
/// hit-testing) reads from this one function, so the dots and the lines can
/// never disagree.
export function portPos(
  node: GraphNode,
  spec: BlockSpec | undefined,
  port: string | "__out__"
): { x: number; y: number } {
  const inputs = spec?.inputs ?? [];
  const index = port === "__out__" ? inputs.length : inputs.findIndex((p) => p.id === port);
  const y = node.y + HEAD_H + BODY_PAD + Math.max(index, 0) * ROW_H + ROW_H / 2;
  return { x: port === "__out__" ? node.x + NODE_W : node.x, y };
}

type DragState =
  | { kind: "none" }
  | { kind: "pan"; startX: number; startY: number; tx: number; ty: number }
  | { kind: "node"; id: string; dx: number; dy: number }
  | { kind: "wire"; from: string; x: number; y: number };

type Props = {
  catalog: Catalog;
  graph: Graph;
  report: Report | null;
  selected: string | null;
  deployed: Record<string, string>;
  onSelect: (id: string | null) => void;
  onChange: (graph: Graph) => void;
};

export default function Canvas({
  catalog,
  graph,
  report,
  selected,
  deployed,
  onSelect,
  onChange,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ tx: 0, ty: 0, scale: 1 });
  const [drag, setDrag] = useState<DragState>({ kind: "none" });
  const [hoverPort, setHoverPort] = useState<string | null>(null);
  const [over, setOver] = useState(false);

  const specOf = useCallback(
    (node: GraphNode) => catalog.blocks.find((b) => b.id === node.block),
    [catalog]
  );

  const toWorld = useCallback(
    (clientX: number, clientY: number) => {
      const rect = ref.current?.getBoundingClientRect();
      if (!rect) return { x: 0, y: 0 };
      return {
        x: (clientX - rect.left - view.tx) / view.scale,
        y: (clientY - rect.top - view.ty) / view.scale,
      };
    },
    [view]
  );

  // Pointer handling lives on the container rather than per-node so a fast drag
  // that outruns the cursor does not drop the node mid-flight.
  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (drag.kind === "pan") {
        setView((v) => ({
          ...v,
          tx: drag.tx + (e.clientX - drag.startX),
          ty: drag.ty + (e.clientY - drag.startY),
        }));
      } else if (drag.kind === "node") {
        const { x, y } = toWorld(e.clientX, e.clientY);
        onChange({
          ...graph,
          nodes: graph.nodes.map((n) =>
            n.id === drag.id ? { ...n, x: Math.round(x - drag.dx), y: Math.round(y - drag.dy) } : n
          ),
        });
      } else if (drag.kind === "wire") {
        const { x, y } = toWorld(e.clientX, e.clientY);
        setDrag({ ...drag, x, y });
      }
    },
    [drag, graph, onChange, toWorld]
  );

  const finishWire = useCallback(
    (to: string, port: string) => {
      if (drag.kind !== "wire" || drag.from === to) return;
      const edges = graph.edges.filter((e) => !(e.to === to && e.port === port));
      onChange({ ...graph, edges: [...edges, { from: drag.from, to, port }] });
    },
    [drag, graph, onChange]
  );

  const onPointerUp = useCallback(() => {
    setDrag({ kind: "none" });
    setHoverPort(null);
  }, []);

  useEffect(() => {
    const stop = () => setDrag({ kind: "none" });
    window.addEventListener("pointerup", stop);
    return () => window.removeEventListener("pointerup", stop);
  }, []);

  const onWheel = useCallback((e: React.WheelEvent) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    setView((v) => {
      const next = Math.min(1.8, Math.max(0.35, v.scale * (e.deltaY < 0 ? 1.08 : 0.926)));
      // Zoom toward the cursor, not the origin.
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      return {
        scale: next,
        tx: px - ((px - v.tx) / v.scale) * next,
        ty: py - ((py - v.ty) / v.scale) * next,
      };
    });
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setOver(false);
      const blockId = e.dataTransfer.getData("application/x-defi-block");
      if (!blockId) return;
      const spec = catalog.blocks.find((b) => b.id === blockId);
      if (!spec) return;
      const { x, y } = toWorld(e.clientX, e.clientY);
      const id = `n${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
      const params: Record<string, any> = {};
      for (const p of spec.params) params[p.name] = p.default;
      onChange({
        ...graph,
        nodes: [
          ...graph.nodes,
          { id, block: blockId, x: Math.round(x - NODE_W / 2), y: Math.round(y - 20), params },
        ],
      });
      onSelect(id);
    },
    [catalog, graph, onChange, onSelect, toWorld]
  );

  const removeEdge = (index: number) =>
    onChange({ ...graph, edges: graph.edges.filter((_, i) => i !== index) });

  const errorPorts = new Set(
    (report?.issues ?? [])
      .filter((i) => i.level === "error" && i.node && i.port)
      .map((i) => `${i.node}:${i.port}`)
  );
  const errorNodes = new Set(
    (report?.issues ?? []).filter((i) => i.level === "error" && i.node).map((i) => i.node!)
  );

  const colorOf = (type: string) => catalog.portTypes[type]?.color ?? "#7a8b9c";

  return (
    <div
      ref={ref}
      className={`canvas${drag.kind === "pan" ? " panning" : ""}${over ? " dropping" : ""}`}
      onPointerDown={(e) => {
        if (e.target === e.currentTarget || (e.target as HTMLElement).tagName === "svg") {
          onSelect(null);
          setDrag({ kind: "pan", startX: e.clientX, startY: e.clientY, tx: view.tx, ty: view.ty });
        }
      }}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onWheel={onWheel}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <div
        style={{
          position: "absolute",
          transformOrigin: "0 0",
          transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
          width: 0,
          height: 0,
        }}
      >
        <svg
          style={{ position: "absolute", overflow: "visible", pointerEvents: "none", left: 0, top: 0 }}
        >
          {graph.edges.map((edge, i) => {
            const from = graph.nodes.find((n) => n.id === edge.from);
            const to = graph.nodes.find((n) => n.id === edge.to);
            if (!from || !to) return null;
            const toSpec = specOf(to);
            const a = portPos(from, specOf(from), "__out__");
            const b = portPos(to, toSpec, edge.port);
            const type = toSpec?.inputs.find((p) => p.id === edge.port)?.type ?? "";
            const bad = errorPorts.has(`${edge.to}:${edge.port}`);
            const dx = Math.max(45, Math.abs(b.x - a.x) * 0.45);
            return (
              <path
                key={`${edge.from}-${edge.to}-${edge.port}-${i}`}
                className="wire"
                d={`M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`}
                stroke={bad ? "#f87171" : colorOf(type)}
                strokeOpacity={bad ? 0.9 : 0.55}
                style={{ pointerEvents: "stroke" }}
                onClick={() => removeEdge(i)}
              >
                <title>{`${from.id} → ${to.id}.${edge.port} — click to disconnect`}</title>
              </path>
            );
          })}

          {drag.kind === "wire" &&
            (() => {
              const from = graph.nodes.find((n) => n.id === drag.from);
              if (!from) return null;
              const a = portPos(from, specOf(from), "__out__");
              const dx = Math.max(45, Math.abs(drag.x - a.x) * 0.45);
              return (
                <path
                  className="wire"
                  d={`M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${drag.x - dx} ${drag.y}, ${drag.x} ${drag.y}`}
                  stroke="var(--accent)"
                  strokeDasharray="4 4"
                  strokeOpacity={0.8}
                />
              );
            })()}
        </svg>

        {graph.nodes.map((node) => {
          const spec = specOf(node);
          const isSelected = selected === node.id;
          const address = deployed[node.id];
          return (
            <div
              key={node.id}
              className={`node${isSelected ? " selected" : ""}${
                errorNodes.has(node.id) ? " invalid" : ""
              }`}
              style={{ left: node.x, top: node.y }}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onSelect(node.id)}
            >
              <div
                className="node-head"
                onPointerDown={(e) => {
                  e.stopPropagation();
                  onSelect(node.id);
                  const { x, y } = toWorld(e.clientX, e.clientY);
                  setDrag({ kind: "node", id: node.id, dx: x - node.x, dy: y - node.y });
                }}
              >
                <div className="node-icon">{spec?.icon ?? "?"}</div>
                <div className="node-title">{node.label || spec?.name || node.block}</div>
                <button
                  className="ghost"
                  style={{ padding: "1px 6px", fontSize: 11, borderColor: "transparent", color: "var(--dim)" }}
                  title="Remove block"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    onChange({
                      ...graph,
                      nodes: graph.nodes.filter((n) => n.id !== node.id),
                      edges: graph.edges.filter((x) => x.from !== node.id && x.to !== node.id),
                    });
                    if (selected === node.id) onSelect(null);
                  }}
                >
                  ×
                </button>
              </div>

              <div className="node-body">
                {(spec?.inputs ?? []).map((port) => {
                  const wired = graph.edges.some((e) => e.to === node.id && e.port === port.id);
                  const missing = port.required && !wired;
                  return (
                    <div
                      className="port-row"
                      key={port.id}
                      onPointerUp={(e) => {
                        e.stopPropagation();
                        finishWire(node.id, port.id);
                        setDrag({ kind: "none" });
                      }}
                      onPointerEnter={() =>
                        drag.kind === "wire" && setHoverPort(`${node.id}:${port.id}`)
                      }
                      onPointerLeave={() => setHoverPort(null)}
                    >
                      <span
                        className={`port in${hoverPort === `${node.id}:${port.id}` ? " armed" : ""}`}
                        style={{
                          background: wired ? colorOf(port.type) : "#101922",
                          borderColor: missing ? "var(--danger)" : "var(--bg)",
                          boxShadow: `0 0 0 1px ${colorOf(port.type)}66`,
                          top: HEAD_H + BODY_PAD + (spec?.inputs.indexOf(port) ?? 0) * ROW_H + ROW_H / 2 - 5.5,
                        }}
                        title={`${port.label} — ${port.type}${port.required ? " (required)" : " (optional)"}`}
                      />
                      <span className={missing ? "missing" : ""}>{port.label}</span>
                      {missing && <span className="missing" style={{ fontSize: 10 }}>needed</span>}
                    </div>
                  );
                })}

                <div
                  className="port-row out"
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    const { x, y } = toWorld(e.clientX, e.clientY);
                    setDrag({ kind: "wire", from: node.id, x, y });
                  }}
                >
                  <span style={{ color: "var(--dim)", fontSize: 10 }}>
                    {(spec?.provides ?? []).join(" · ") || "—"}
                  </span>
                  <span
                    className="port out"
                    style={{
                      background: colorOf(spec?.provides?.[0] ?? ""),
                      top:
                        HEAD_H + BODY_PAD + (spec?.inputs.length ?? 0) * ROW_H + ROW_H / 2 - 5.5,
                    }}
                    title="Drag from here into another block's port"
                  />
                </div>

                {address && (
                  <div className="mono-small" style={{ padding: "5px 10px 0", color: "var(--accent)" }}>
                    {address.slice(0, 10)}…{address.slice(-6)}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {graph.nodes.length === 0 && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            pointerEvents: "none",
            color: "var(--dim)",
            textAlign: "center",
            lineHeight: 1.9,
          }}
        >
          <div>
            <div style={{ fontSize: 26, marginBottom: 10, opacity: 0.5 }}>✦</div>
            drag a block in from the left
            <br />
            <span style={{ fontSize: 11 }}>
              or open a template, or describe what you want in AI COMPOSE
            </span>
          </div>
        </div>
      )}

      <div style={{ position: "absolute", left: 12, bottom: 12, display: "flex", gap: 6 }}>
        <button className="ghost" onClick={() => setView({ tx: 0, ty: 0, scale: 1 })}>
          reset view
        </button>
        <span className="pill">{Math.round(view.scale * 100)}%</span>
      </div>
    </div>
  );
}
