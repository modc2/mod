"use client";

import { useMemo, useState } from "react";
import type { BlockSpec, Catalog } from "../lib/types";

type Props = {
  catalog: Catalog;
  onInspect: (block: BlockSpec) => void;
  onAdd: (block: BlockSpec) => void;
};

export default function Palette({ catalog, onInspect, onAdd }: Props) {
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = catalog.blocks.filter(
      (b) =>
        !needle ||
        b.name.toLowerCase().includes(needle) ||
        b.summary.toLowerCase().includes(needle) ||
        b.category.toLowerCase().includes(needle) ||
        b.provides.some((p) => p.includes(needle)) ||
        b.inputs.some((i) => i.type.includes(needle))
    );
    const out = new Map<string, BlockSpec[]>();
    for (const block of matched) {
      if (!out.has(block.category)) out.set(block.category, []);
      out.get(block.category)!.push(block);
    }
    return Array.from(out.entries());
  }, [catalog, query]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: 10, borderBottom: "1px solid var(--line)" }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search blocks or port types…"
        />
      </div>

      <div className="scroll" style={{ flex: 1, padding: 10 }}>
        {groups.map(([category, blocks]) => (
          <div key={category} style={{ marginBottom: 16 }}>
            <div className="label" style={{ marginBottom: 7 }}>
              {category}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {blocks.map((block) => (
                <div
                  key={block.id}
                  className="card click"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData("application/x-defi-block", block.id);
                    e.dataTransfer.effectAllowed = "copy";
                  }}
                  onDoubleClick={() => onAdd(block)}
                  onClick={() => onInspect(block)}
                  title="Drag onto the canvas — or double-click to drop it in the middle"
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 14 }}>{block.icon}</span>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{block.name}</span>
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--muted)",
                      marginTop: 5,
                      lineHeight: 1.45,
                    }}
                  >
                    {block.summary}
                  </div>
                  <div style={{ display: "flex", gap: 4, marginTop: 7, flexWrap: "wrap" }}>
                    {block.inputs
                      .filter((i) => i.required)
                      .map((i) => (
                        <span
                          key={i.id}
                          className="pill"
                          style={{ color: catalog.portTypes[i.type]?.color }}
                        >
                          ←{i.type}
                        </span>
                      ))}
                    {block.provides.map((p) => (
                      <span key={p} className="pill" style={{ color: catalog.portTypes[p]?.color }}>
                        {p}→
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
        {groups.length === 0 && (
          <div style={{ color: "var(--dim)", fontSize: 11, padding: 12, textAlign: "center" }}>
            nothing matches “{query}”
          </div>
        )}
      </div>
    </div>
  );
}
