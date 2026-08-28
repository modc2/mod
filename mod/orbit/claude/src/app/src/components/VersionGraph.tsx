"use client";

// VersionGraph — the VERSIONS tab's "graph" sub-view: a module's history as
// structure instead of a list.
//
// Two halves, both hand-drawn SVG (no chart lib, same as FileGraph):
//
//  · the SPINE — every version as a node on a git-style lane graph. Time runs
//    left to right; a record whose parent isn't the lane's head opens a new
//    lane, so forks and rollbacks are visible as branches rather than as a
//    word in a row. Click a node to select it.
//
//  · the ONTOLOGY — what the selected version actually changed, fetched from
//    /modules/:name/ontology (a diff of two snapshot manifests) and drawn as a
//    tree: the version at the root, directories fanning right, files as
//    leaves. Straight-line directory chains collapse into one node
//    ("src/app/app/components") so the shape shows the change, not the
//    filesystem's indentation. Colour is the status — added / modified /
//    deleted — and a directory carries the counts of what's under it.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type VersionRecord = {
  cid: string;
  message: string;
  author: string;
  timestamp: number;
  parent: string | null;
  action?: string;
};

type Change = { path: string; status: "added" | "modified" | "deleted" | string; size: number };

type Ontology = {
  cid: string;
  base: string | null;
  base_missing?: boolean;
  message?: string | null;
  action?: string | null;
  summary: { added: number; modified: number; deleted: number; total: number };
  truncated?: boolean;
  changes: Change[];
};

const STATUS_COLOR: Record<string, string> = {
  added: "var(--crt-green)",
  modified: "var(--crt-amber)",
  deleted: "var(--crt-red)",
};

const ACTION_COLOR: Record<string, string> = {
  snapshot: "var(--accent-color)",
  edit: "var(--accent-color-2)",
  restore: "var(--crt-amber)",
  fork: "var(--crt-blue)",
  copy: "var(--crt-blue)",
  import: "var(--crt-blue)",
  "auto-snapshot": "var(--text-tertiary)",
};

// ── Spine layout ─────────────────────────────────────────────────────

const SP_COL = 34; // horizontal distance between consecutive versions
const SP_LANE = 26; // vertical distance between branch lanes
const SP_PAD = 18;

type SpineNode = { v: VersionRecord; i: number; lane: number; x: number; y: number };

function layoutSpine(versions: VersionRecord[]) {
  // Lane heads: cid of the newest version placed on each lane. A version
  // extends the lane its parent sits at the head of; anything else (a fork
  // point, a re-rooted import) starts a lane of its own.
  const heads: (string | null)[] = [];
  const nodes: SpineNode[] = [];
  versions.forEach((v, i) => {
    let lane = v.parent ? heads.findIndex((h) => h === v.parent) : -1;
    if (lane < 0) {
      lane = heads.findIndex((h) => h === null);
      if (lane < 0) lane = heads.length;
    }
    heads[lane] = v.cid;
    nodes.push({ v, i, lane, x: SP_PAD + i * SP_COL, y: SP_PAD + lane * SP_LANE });
  });
  const byCid = new Map(nodes.map((n) => [n.v.cid, n] as const));
  const links = nodes
    .map((n) => (n.v.parent ? { from: byCid.get(n.v.parent), to: n } : null))
    .filter((l): l is { from: SpineNode; to: SpineNode } => !!l?.from);
  const lanes = heads.length || 1;
  return {
    nodes,
    links,
    width: SP_PAD * 2 + Math.max(0, versions.length - 1) * SP_COL,
    height: SP_PAD * 2 + (lanes - 1) * SP_LANE,
  };
}

// ── Ontology tree ────────────────────────────────────────────────────

type TreeNode = {
  key: string;
  label: string;
  isFile: boolean;
  status?: string;
  size: number;
  added: number;
  modified: number;
  deleted: number;
  children: TreeNode[];
};

function blankNode(key: string, label: string, isFile = false): TreeNode {
  return { key, label, isFile, size: 0, added: 0, modified: 0, deleted: 0, children: [] };
}

/// Build the directory trie, then collapse every straight-line chain so
/// `src/app/app/components/Foo.tsx` costs one directory node, not four.
function buildTree(changes: Change[], maxLeaves: number): { root: TreeNode; dropped: number } {
  const root = blankNode("", "");
  const shown = changes.slice(0, maxLeaves);
  for (const c of shown) {
    const parts = c.path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;
      const key = parts.slice(0, i + 1).join("/");
      let next = node.children.find((n) => n.key === key);
      if (!next) {
        next = blankNode(key, part, isFile);
        node.children.push(next);
      }
      if (isFile) {
        next.status = c.status;
        next.size = c.size;
      }
      node = next;
    });
  }
  const roll = (n: TreeNode): void => {
    for (const c of n.children) roll(c);
    if (n.isFile) {
      if (n.status === "added") n.added = 1;
      else if (n.status === "deleted") n.deleted = 1;
      else n.modified = 1;
      return;
    }
    for (const c of n.children) {
      n.added += c.added;
      n.modified += c.modified;
      n.deleted += c.deleted;
      n.size += c.size;
    }
  };
  roll(root);
  const collapse = (n: TreeNode): TreeNode => {
    let cur = n;
    while (!cur.isFile && cur.children.length === 1 && !cur.children[0].isFile) {
      const only = cur.children[0];
      cur = { ...only, label: `${cur.label}/${only.label}`.replace(/^\//, "") };
    }
    return { ...cur, children: cur.children.map(collapse) };
  };
  const collapsed = collapse(root);
  return { root: collapsed, dropped: changes.length - shown.length };
}

const COL_W = 190;
const ROW_H = 22;
const T_PAD_X = 14;
const T_PAD_Y = 12;
const MAX_LEAVES = 240;

type Placed = TreeNode & { x: number; y: number; parent?: Placed };

function layoutTree(root: TreeNode) {
  const placed: Placed[] = [];
  const links: Array<{ from: Placed; to: Placed }> = [];
  let row = 0;
  let maxDepth = 0;
  const walk = (n: TreeNode, depth: number, parent?: Placed): Placed => {
    maxDepth = Math.max(maxDepth, depth);
    const self: Placed = { ...n, x: T_PAD_X + depth * COL_W, y: 0, parent };
    if (n.children.length === 0) {
      self.y = T_PAD_Y + row * ROW_H;
      row++;
    } else {
      const kids = n.children.map((c) => walk(c, depth + 1, self));
      self.y = (kids[0].y + kids[kids.length - 1].y) / 2;
      for (const k of kids) links.push({ from: self, to: k });
    }
    placed.push(self);
    return self;
  };
  walk(root, 0);
  return {
    nodes: placed,
    links,
    width: T_PAD_X * 2 + (maxDepth + 1) * COL_W,
    height: T_PAD_Y * 2 + Math.max(1, row) * ROW_H,
  };
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function shortTime(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ── Component ────────────────────────────────────────────────────────

export function VersionGraph({
  apiBase,
  module,
  versions,
}: {
  apiBase: string;
  module: string;
  versions: VersionRecord[];
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [onto, setOnto] = useState<Ontology | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spineRef = useRef<HTMLDivElement>(null);

  const head = versions.length ? versions[versions.length - 1].cid : null;
  const activeCid = selected && versions.some((v) => v.cid === selected) ? selected : head;

  // Newest version first: scroll the spine to its right edge on load.
  useEffect(() => {
    const el = spineRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [versions.length]);

  const loadOntology = useCallback(
    async (cid: string) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(
          `${apiBase}/modules/${encodeURIComponent(module)}/ontology?cid=${encodeURIComponent(cid)}`,
        );
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        setOnto(d);
      } catch (e) {
        setOnto(null);
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [apiBase, module],
  );

  useEffect(() => {
    if (activeCid) loadOntology(activeCid);
    else setOnto(null);
  }, [activeCid, loadOntology]);

  const spine = useMemo(() => layoutSpine(versions), [versions]);
  const tree = useMemo(() => {
    if (!onto || onto.changes.length === 0) return null;
    const { root, dropped } = buildTree(onto.changes, MAX_LEAVES);
    return { ...layoutTree(root), dropped };
  }, [onto]);

  const record = versions.find((v) => v.cid === activeCid) || null;

  return (
    <div className="vgr">
      {/* ── spine ── */}
      <div className="vgr-spine" ref={spineRef}>
        <svg width={spine.width} height={spine.height} style={{ display: "block", overflow: "visible" }}>
          {spine.links.map((l, i) => (
            <path
              key={i}
              d={
                l.from.lane === l.to.lane
                  ? `M ${l.from.x} ${l.from.y} L ${l.to.x} ${l.to.y}`
                  : `M ${l.from.x} ${l.from.y} C ${l.from.x + SP_COL / 2} ${l.from.y}, ${l.to.x - SP_COL / 2} ${l.to.y}, ${l.to.x} ${l.to.y}`
              }
              fill="none"
              stroke="color-mix(in srgb, var(--text-tertiary) 45%, transparent)"
              strokeWidth={1.3}
            />
          ))}
          {spine.nodes.map((n) => {
            const color = ACTION_COLOR[n.v.action || "snapshot"] || ACTION_COLOR.snapshot;
            const on = n.v.cid === activeCid;
            return (
              <g
                key={n.v.cid + n.v.timestamp}
                transform={`translate(${n.x},${n.y})`}
                onClick={() => setSelected(n.v.cid)}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {`v${n.i + 1} · ${n.v.action || "snapshot"} · ${shortTime(n.v.timestamp)}\n${n.v.message || "(no message)"}`}
                </title>
                {on && <circle r={9} fill="none" stroke={color} strokeWidth={1.2} opacity={0.7} />}
                <circle r={on ? 5.5 : 4} fill={color} opacity={on ? 1 : 0.75} />
                <circle r={11} fill="transparent" />
              </g>
            );
          })}
        </svg>
      </div>

      {/* ── selected version header ── */}
      {record && (
        <div className="vgr-head">
          <span className="vgr-dot" style={{ background: ACTION_COLOR[record.action || "snapshot"] }} />
          <span className="vgr-msg" title={record.message}>{record.message || "(no message)"}</span>
          <span className="vgr-meta">{shortTime(record.timestamp)}</span>
          {onto && (
            <span className="vgr-counts">
              <span style={{ color: "var(--crt-green)" }}>+{onto.summary.added}</span>
              <span style={{ color: "var(--crt-amber)" }}>~{onto.summary.modified}</span>
              <span style={{ color: "var(--crt-red)" }}>−{onto.summary.deleted}</span>
            </span>
          )}
        </div>
      )}

      {/* ── change ontology ── */}
      <div className="vgr-tree">
        {loading && <div className="vgr-note">reading manifests…</div>}
        {error && <div className="vgr-note err">✗ {error}</div>}
        {!loading && !error && onto && onto.changes.length === 0 && (
          <div className="vgr-note">
            {onto.base_missing
              ? "the tree this version came from is no longer in the store — nothing to diff against"
              : "no file changes between this version and its parent"}
          </div>
        )}
        {!loading && tree && (
          <svg width={tree.width} height={tree.height} style={{ display: "block", overflow: "visible" }}>
            {tree.links.map((l, i) => (
              <path
                key={i}
                d={`M ${l.from.x + 6} ${l.from.y} C ${l.from.x + COL_W / 2} ${l.from.y}, ${l.to.x - COL_W / 2} ${l.to.y}, ${l.to.x - 4} ${l.to.y}`}
                fill="none"
                stroke="color-mix(in srgb, var(--text-tertiary) 30%, transparent)"
                strokeWidth={1}
              />
            ))}
            {tree.nodes.map((n) => {
              const color = n.isFile
                ? STATUS_COLOR[n.status || "modified"]
                : "color-mix(in srgb, var(--text-tertiary) 80%, transparent)";
              const total = n.added + n.modified + n.deleted;
              return (
                <g key={n.key || "$root"} transform={`translate(${n.x},${n.y})`}>
                  <title>
                    {n.isFile
                      ? `${n.status} · ${n.key} · ${fmtBytes(n.size)}`
                      : `${n.key || module} · +${n.added} ~${n.modified} −${n.deleted}`}
                  </title>
                  {n.isFile ? (
                    <circle r={3.5} fill={color} />
                  ) : (
                    <rect x={-3.5} y={-3.5} width={7} height={7} rx={1.5} fill={color} opacity={0.8} />
                  )}
                  <text
                    x={10}
                    y={3.5}
                    className="vgr-label"
                    fill={n.isFile ? "var(--text-secondary)" : "var(--text-tertiary)"}
                    fontWeight={n.isFile ? 400 : 600}
                  >
                    {n.label || module}
                    {!n.isFile && total > 0 && (
                      <tspan fill="var(--text-tertiary)" opacity={0.55}> · {total}</tspan>
                    )}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        {tree && tree.dropped > 0 && (
          <div className="vgr-note">+{tree.dropped} more changed files not drawn</div>
        )}
      </div>

      <div className="vgr-legend">
        {(["added", "modified", "deleted"] as const).map((s) => (
          <span key={s}>
            <i style={{ background: STATUS_COLOR[s] }} /> {s}
          </span>
        ))}
        <span className="vgr-legend-sep" />
        {Object.entries(ACTION_COLOR)
          .filter(([k]) => versions.some((v) => (v.action || "snapshot") === k))
          .map(([k, c]) => (
            <span key={k}>
              <i style={{ background: c, borderRadius: "50%" }} /> {k}
            </span>
          ))}
      </div>
    </div>
  );
}
