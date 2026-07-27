"use client";

// FileGraph — the CODE tab's "Graph" sub-view: the module's file tree drawn
// as a horizontal node-link diagram (root at the left, directories fanning
// out to the right) for people who parse structure visually faster than an
// indented list. Same data as the Files list (/files/tree), same per-type
// identity colors, so a .rs file is the same hue in both views.
//
// The /files/tree payload is depth-limited, so deep directories arrive with
// no children — clicking one lazy-loads its subtree via loadChildren and
// grafts it in (grafts are kept locally so periodic tree refreshes don't
// stomp them). Clicking any node also opens the inspector panel on the
// right: files show size/CID plus a content preview (loadContent) with an
// "open in editor" escape hatch; directories show subtree stats and a
// clickable child list. Labels stay in text tokens — the colored mark alone
// carries file-type identity — and edges are recessive hairlines so the
// structure, not the plumbing, is what pops.

import { useEffect, useMemo, useRef, useState } from "react";

export interface FileGraphNode {
  name: string;
  path: string;
  type: "file" | "directory";
  cid?: string | null;
  size?: number | null;
  children?: FileGraphNode[];
}

interface PlacedNode {
  name: string;
  path: string;
  isDir: boolean;
  isRoot: boolean;
  expanded: boolean;
  kidsKnown: boolean; // false = children never loaded (depth-truncated)
  fileCount: number; // files in subtree (collapsed-dir badge)
  cid?: string | null;
  size?: number | null;
  x: number;
  y: number;
  parent?: PlacedNode;
}

const COL_W = 172; // horizontal distance between depth columns
const ROW_H = 24; // vertical distance between leaf rows
const PAD_X = 18;
const PAD_Y = 16;
const LABEL_MAX = 22; // truncate labels beyond this many chars
const AUTO_EXPAND_BUDGET = 140; // initial auto-expand stops near this many visible nodes
const PREVIEW_MAX_CHARS = 60_000; // inspector preview cap — full file lives in the editor

const ROOT_PATH = "$graph-root";

function subtreeStats(node: FileGraphNode): { files: number; dirs: number; bytes: number } {
  if (node.type === "file") return { files: 1, dirs: 0, bytes: node.size || 0 };
  const acc = { files: 0, dirs: 0, bytes: 0 };
  for (const c of node.children || []) {
    const s = subtreeStats(c);
    acc.files += s.files;
    acc.dirs += s.dirs + (c.type === "directory" ? 1 : 0);
    acc.bytes += s.bytes;
  }
  return acc;
}

function fmtSize(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Breadth-first initial expansion: open directories level by level until the
// visible node count would blow past the budget, so small modules open fully
// and big ones start readable instead of as a 900-row wall.
function initialExpanded(tree: FileGraphNode[]): Set<string> {
  const expanded = new Set<string>([ROOT_PATH]);
  let visible = tree.length;
  let frontier = tree.filter((n) => n.type === "directory" && n.children?.length);
  while (frontier.length) {
    const next: FileGraphNode[] = [];
    for (const dir of frontier) {
      const kids = dir.children || [];
      if (visible + kids.length > AUTO_EXPAND_BUDGET) return expanded;
      expanded.add(dir.path);
      visible += kids.length;
      next.push(...kids.filter((n) => n.type === "directory" && n.children?.length));
    }
    frontier = next;
  }
  return expanded;
}

function truncateLabel(name: string): string {
  return name.length > LABEL_MAX ? name.slice(0, LABEL_MAX - 1) + "…" : name;
}

export default function FileGraph({
  tree,
  rootLabel,
  fileColor,
  onOpenFile,
  loadChildren,
  loadContent,
  onRefresh,
  emptyMessage,
}: {
  tree: FileGraphNode[];
  rootLabel: string;
  fileColor: (filename: string) => string;
  onOpenFile: (path: string) => void;
  // Fetch the subtree of a depth-truncated directory (same /files/tree shape).
  loadChildren?: (path: string) => Promise<FileGraphNode[]>;
  // Fetch a file's text for the inspector preview. Absent → clicks fall back
  // to onOpenFile directly.
  loadContent?: (path: string) => Promise<string>;
  onRefresh?: () => void;
  emptyMessage?: string | null;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => initialExpanded(tree));
  const [scale, setScale] = useState(1);
  const [hover, setHover] = useState<PlacedNode | null>(null);
  // Lazily-fetched subtrees keyed by dir path — kept out of the parent's tree
  // state so its periodic refreshes can't wipe them.
  const [grafts, setGrafts] = useState<Map<string, FileGraphNode[]>>(new Map());
  const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ path: string; text: string; loading: boolean; error?: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Re-seed everything when the underlying dir changes (module switch /
  // refresh with different content), keyed by the root paths.
  const treeKey = useMemo(() => tree.map((n) => n.path).join("\n"), [tree]);
  useEffect(() => {
    setExpanded(initialExpanded(tree));
    setHover(null);
    setGrafts(new Map());
    setLoadingDirs(new Set());
    setSelectedPath(null);
    setPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeKey]);

  // The prop tree with lazy-loaded subtrees grafted in.
  const mergedTree = useMemo(() => {
    const merge = (nodes: FileGraphNode[]): FileGraphNode[] =>
      nodes.map((n) => {
        if (n.type !== "directory") return n;
        const kids = n.children?.length ? n.children : grafts.get(n.path) ?? n.children ?? [];
        return { ...n, children: merge(kids) };
      });
    return grafts.size === 0 ? tree : merge(tree);
  }, [tree, grafts]);

  // path → node index over the merged tree, for the inspector panel.
  const nodeByPath = useMemo(() => {
    const map = new Map<string, FileGraphNode>();
    const walk = (list: FileGraphNode[]) => {
      for (const n of list) {
        map.set(n.path, n);
        if (n.children) walk(n.children);
      }
    };
    walk(mergedTree);
    return map;
  }, [mergedTree]);

  const { nodes, edges, width, height, dirCount, fileCount } = useMemo(() => {
    const placed: PlacedNode[] = [];
    const links: Array<{ from: PlacedNode; to: PlacedNode }> = [];
    let leafRow = 0;
    let maxDepth = 0;
    let dirs = 0;
    let files = 0;

    const layout = (node: FileGraphNode, depth: number, parent?: PlacedNode): PlacedNode => {
      const isDir = node.type === "directory";
      if (isDir) dirs++; else files++;
      maxDepth = Math.max(maxDepth, depth);
      const kids = node.children || [];
      const isOpen = isDir && kids.length > 0 && expanded.has(node.path);
      const stats = isDir ? subtreeStats(node) : null;
      const self: PlacedNode = {
        name: node.name,
        path: node.path,
        isDir,
        isRoot: false,
        expanded: isOpen,
        kidsKnown: !isDir || kids.length > 0 || grafts.has(node.path),
        fileCount: stats ? stats.files : 0,
        cid: node.cid,
        size: node.size,
        x: PAD_X + depth * COL_W,
        y: 0,
        parent,
      };
      if (isOpen) {
        const placedKids = kids.map((c) => layout(c, depth + 1, self));
        self.y = (placedKids[0].y + placedKids[placedKids.length - 1].y) / 2;
        placedKids.forEach((k) => links.push({ from: self, to: k }));
      } else {
        self.y = PAD_Y + leafRow * ROW_H;
        leafRow++;
      }
      placed.push(self);
      return self;
    };

    // Synthetic root: the browsed directory itself, always open.
    const root: PlacedNode = {
      name: rootLabel,
      path: ROOT_PATH,
      isDir: true,
      isRoot: true,
      expanded: true,
      kidsKnown: true,
      fileCount: 0,
      x: PAD_X,
      y: PAD_Y,
    };
    if (mergedTree.length > 0) {
      const placedKids = mergedTree.map((c) => layout(c, 1, root));
      root.y = (placedKids[0].y + placedKids[placedKids.length - 1].y) / 2;
      placedKids.forEach((k) => links.push({ from: root, to: k }));
    }
    placed.push(root);

    return {
      nodes: placed,
      edges: links,
      width: PAD_X * 2 + (maxDepth + 1) * COL_W + 40,
      height: Math.max(PAD_Y * 2 + Math.max(leafRow, 1) * ROW_H, 120),
      dirCount: dirs,
      fileCount: files,
    };
  }, [mergedTree, expanded, rootLabel, grafts]);

  // Legend: the extensions actually on screen, most common first — identity
  // is already in the label text, so this is a redundant color key, capped
  // so it stays a single quiet row.
  const legend = useMemo(() => {
    const counts = new Map<string, { color: string; n: number }>();
    const walk = (list: FileGraphNode[]) => {
      for (const n of list) {
        if (n.type === "file") {
          const dot = n.name.lastIndexOf(".");
          const ext = dot === -1 ? "·" : n.name.slice(dot).toLowerCase();
          const cur = counts.get(ext);
          if (cur) cur.n++;
          else counts.set(ext, { color: fileColor(n.name), n: 1 });
        } else if (n.children) walk(n.children);
      }
    };
    walk(mergedTree);
    return Array.from(counts.entries())
      .sort((a, b) => b[1].n - a[1].n)
      .slice(0, 9);
  }, [mergedTree, fileColor]);

  const toggleDir = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  // Fetch and graft a depth-truncated directory's subtree, then open it.
  const fetchDir = (path: string) => {
    if (!loadChildren || loadingDirs.has(path)) return;
    setLoadingDirs((prev) => new Set(prev).add(path));
    loadChildren(path)
      .then((kids) => {
        setGrafts((prev) => new Map(prev).set(path, kids));
        setExpanded((prev) => new Set(prev).add(path));
      })
      .catch(() => {})
      .finally(() =>
        setLoadingDirs((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        })
      );
  };

  const previewFile = (path: string) => {
    if (!loadContent) {
      onOpenFile(path);
      return;
    }
    setPreview({ path, text: "", loading: true });
    loadContent(path)
      .then((text) =>
        setPreview((p) => (p?.path === path ? { path, text: text.slice(0, PREVIEW_MAX_CHARS), loading: false } : p))
      )
      .catch((e: any) =>
        setPreview((p) => (p?.path === path ? { path, text: "", loading: false, error: e?.message || "Failed to load" } : p))
      );
  };

  const clickNode = (n: { path: string; isDir: boolean; kidsKnown?: boolean }) => {
    setSelectedPath(n.path);
    if (n.isDir) {
      setPreview(null);
      if (n.kidsKnown === false) fetchDir(n.path);
      else toggleDir(n.path);
    } else {
      previewFile(n.path);
    }
  };

  const copyMeta = (key: string, value: string) => {
    try {
      navigator.clipboard?.writeText(value);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 1200);
    } catch {}
  };

  const expandAll = () => {
    const all = new Set<string>([ROOT_PATH]);
    const walk = (list: FileGraphNode[]) => {
      for (const n of list) {
        if (n.type === "directory" && n.children?.length) {
          all.add(n.path);
          walk(n.children);
        }
      }
    };
    walk(mergedTree);
    setExpanded(all);
  };

  if (tree.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6">
        <span className="text-[42px]" style={{ color: "var(--crt-green)", opacity: 0.12 }}>⌬</span>
        <span className="text-[13px] uppercase" style={{ color: "var(--text-tertiary)", letterSpacing: "0.02em" }}>
          {emptyMessage || "No files to graph"}
        </span>
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="text-[11px] px-3 py-1.5 border border-crt-green/30 text-crt-green/60 hover:text-crt-green hover:border-crt-green transition-all uppercase font-code"
          >
            ↻ Refresh
          </button>
        )}
      </div>
    );
  }

  const zoomBtnCls =
    "text-[12px] w-6 h-6 flex items-center justify-center border border-crt-green/20 text-crt-green/50 hover:text-crt-green hover:border-crt-green/50 transition-all font-code";

  const selectedNode = selectedPath ? nodeByPath.get(selectedPath) : null;
  const selectedStats = selectedNode?.type === "directory" ? subtreeStats(selectedNode) : null;

  const metaRow = (label: string, value: string, copyValue?: string) => (
    <div className="flex items-start gap-2 min-w-0">
      <span className="text-[9px] uppercase tracking-[0.12em] shrink-0 w-9 pt-[2px]" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
        {label}
      </span>
      <span
        className={`text-[10.5px] font-code break-all min-w-0 ${copyValue ? "cursor-pointer hover:underline" : ""}`}
        style={{ color: copiedKey === label ? "var(--crt-green)" : "var(--text-secondary)" }}
        title={copyValue ? "Click to copy" : undefined}
        onClick={copyValue ? () => copyMeta(label, copyValue) : undefined}
      >
        {copiedKey === label ? "copied ✓" : value}
      </span>
    </div>
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden min-h-0">
      {/* Controls row — zoom + expand state + subtree stats. */}
      <div
        className="flex items-center gap-1.5 px-3 py-1.5 shrink-0 flex-wrap"
        style={{ borderBottom: "1px solid var(--border-color)" }}
      >
        <span className="text-[10px] uppercase font-code tracking-[0.12em]" style={{ color: "var(--text-tertiary)" }}>
          {fileCount} files · {dirCount} dirs
        </span>
        <span className="text-[10px] font-code hidden sm:inline" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
          — click a folder to open/close it, a file to inspect it
        </span>
        <div className="flex-1" />
        <button onClick={expandAll} className="text-[10px] px-2 h-6 border border-crt-amber/25 text-crt-amber/60 hover:text-crt-amber hover:border-crt-amber/60 transition-all uppercase font-code">
          Expand all
        </button>
        <button onClick={() => setExpanded(initialExpanded(mergedTree))} className="text-[10px] px-2 h-6 border border-crt-amber/25 text-crt-amber/60 hover:text-crt-amber hover:border-crt-amber/60 transition-all uppercase font-code">
          Reset
        </button>
        <button onClick={() => setScale((s) => Math.max(0.5, +(s - 0.15).toFixed(2)))} className={zoomBtnCls} title="Zoom out">−</button>
        <span className="text-[10px] font-code w-9 text-center" style={{ color: "var(--text-tertiary)" }}>
          {Math.round(scale * 100)}%
        </span>
        <button onClick={() => setScale((s) => Math.min(2, +(s + 0.15).toFixed(2)))} className={zoomBtnCls} title="Zoom in">+</button>
      </div>

      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* The graph itself — scrolls both ways; zoom rescales the rendered
            size against a fixed viewBox. */}
        <div ref={scrollRef} className="flex-1 overflow-auto relative min-h-0">
          <div className="relative" style={{ width: width * scale, height: height * scale }}>
            <svg
              width={width * scale}
              height={height * scale}
              viewBox={`0 0 ${width} ${height}`}
              style={{ display: "block" }}
            >
              {/* Edges first (recessive hairlines under the marks). */}
              {edges.map((e) => {
                const midX = (e.from.x + e.to.x) / 2;
                return (
                  <path
                    key={`${e.from.path}→${e.to.path}`}
                    d={`M ${e.from.x} ${e.from.y} C ${midX} ${e.from.y}, ${midX} ${e.to.y}, ${e.to.x} ${e.to.y}`}
                    fill="none"
                    stroke={hover && (hover.path === e.to.path || hover.path === e.from.path) ? "color-mix(in srgb, var(--crt-green) 45%, transparent)" : "var(--border-color)"}
                    strokeWidth={1}
                  />
                );
              })}
              {nodes.map((n) => {
                const isHover = hover?.path === n.path;
                const isSelected = selectedPath === n.path;
                const isLoading = loadingDirs.has(n.path);
                const color = n.isRoot
                  ? "var(--crt-green)"
                  : n.isDir
                    ? "var(--crt-amber)"
                    : fileColor(n.name);
                return (
                  <g
                    key={n.path}
                    transform={`translate(${n.x}, ${n.y})`}
                    onClick={() => (n.isRoot ? undefined : clickNode(n))}
                    onMouseEnter={() => setHover(n)}
                    onMouseLeave={() => setHover((h) => (h?.path === n.path ? null : h))}
                    style={{ cursor: n.isRoot ? "default" : "pointer" }}
                  >
                    {/* Oversized invisible hit target (bigger than the mark). */}
                    <rect x={-10} y={-11} width={Math.min(n.name.length, LABEL_MAX) * 6.6 + 26} height={22} fill="transparent" />
                    {/* Selection halo — the inspector's current subject. */}
                    {isSelected && (
                      <circle
                        r={9}
                        fill="none"
                        stroke="color-mix(in srgb, var(--crt-green) 55%, transparent)"
                        strokeWidth={1.4}
                      />
                    )}
                    {n.isDir ? (
                      <rect
                        x={-4.5}
                        y={-4.5}
                        width={9}
                        height={9}
                        rx={2.5}
                        style={{
                          fill: n.expanded || n.isRoot
                            ? `color-mix(in srgb, ${color} 26%, transparent)`
                            : "transparent",
                          stroke: color,
                          strokeWidth: isHover || isSelected ? 1.8 : 1.2,
                          opacity: isLoading ? 0.5 : 1,
                        }}
                      />
                    ) : (
                      <circle
                        r={isHover ? 5.5 : 4.5}
                        style={{
                          fill: color,
                          // White rim keeps dark linguist hues visible on the
                          // near-black surface.
                          stroke: "rgba(255,255,255,0.28)",
                          strokeWidth: 1,
                        }}
                      />
                    )}
                    <text
                      x={n.isDir && n.expanded && !n.isRoot ? -9 : 10}
                      y={n.isDir && n.expanded && !n.isRoot ? -8 : 3.5}
                      textAnchor={n.isDir && n.expanded && !n.isRoot ? "end" : "start"}
                      style={{
                        fontFamily: "var(--font-code, monospace)",
                        fontSize: n.isDir ? 11 : 10.5,
                        fontWeight: n.isDir ? 700 : 400,
                        fill: n.isRoot
                          ? "var(--crt-green)"
                          : n.isDir
                            ? "var(--text-primary)"
                            : isHover || isSelected
                              ? "var(--text-primary)"
                              : "var(--text-secondary)",
                      }}
                    >
                      {truncateLabel(n.name)}
                      {n.isDir && !n.expanded && !n.isRoot && (
                        <tspan style={{ fill: "var(--text-tertiary)", fontWeight: 400 }}>
                          {isLoading ? " ⋯" : !n.kidsKnown ? " (＋)" : n.fileCount > 0 ? ` (${n.fileCount})` : ""}
                        </tspan>
                      )}
                    </text>
                  </g>
                );
              })}
            </svg>

            {/* Hover tooltip — full path (labels truncate) + type/size + CID. */}
            {hover && !hover.isRoot && (
              <div
                className="absolute pointer-events-none px-2 py-1.5 rounded-md font-code text-[10.5px] leading-snug z-10"
                style={{
                  left: Math.min(hover.x * scale + 14, width * scale - 240),
                  top: hover.y * scale + 12,
                  maxWidth: 280,
                  background: "color-mix(in srgb, var(--bg-primary) 92%, transparent)",
                  border: "1px solid var(--border-color-strong, var(--border-color))",
                  color: "var(--text-secondary)",
                  backdropFilter: "blur(6px)",
                }}
              >
                <div className="break-all" style={{ color: "var(--text-primary)" }}>{hover.path}</div>
                <div style={{ color: "var(--text-tertiary)" }}>
                  {hover.isDir
                    ? loadingDirs.has(hover.path)
                      ? "loading contents…"
                      : !hover.kidsKnown
                        ? "not loaded — click to load"
                        : `${hover.fileCount} file${hover.fileCount === 1 ? "" : "s"} — click to ${hover.expanded ? "collapse" : "expand"}`
                    : `${fmtSize(hover.size)} — click to inspect`}
                  {hover.cid ? ` · ${hover.cid.slice(0, 10)}` : ""}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Inspector — metadata + preview for the clicked node. */}
        {selectedNode && (
          <div
            className="w-[320px] max-w-[45%] shrink-0 flex flex-col min-h-0"
            style={{ borderLeft: "1px solid var(--border-color)", background: "color-mix(in srgb, var(--bg-primary) 55%, transparent)" }}
          >
            <div className="flex items-center gap-2 px-3 py-2 shrink-0" style={{ borderBottom: "1px solid var(--border-color)" }}>
              {selectedNode.type === "directory" ? (
                <span
                  className="inline-block rounded-[3px] shrink-0"
                  style={{ width: 9, height: 9, border: "1.2px solid var(--crt-amber)", background: "color-mix(in srgb, var(--crt-amber) 26%, transparent)" }}
                />
              ) : (
                <span
                  className="inline-block rounded-full shrink-0"
                  style={{ width: 9, height: 9, background: fileColor(selectedNode.name), boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.28)" }}
                />
              )}
              <span className="text-[12px] font-code font-bold truncate flex-1 min-w-0" style={{ color: "var(--text-primary)" }} title={selectedNode.name}>
                {selectedNode.name}
              </span>
              {selectedNode.type === "file" && (
                <button
                  onClick={() => onOpenFile(selectedNode.path)}
                  className="text-[9.5px] px-2 h-5 border border-crt-green/30 text-crt-green/70 hover:text-crt-green hover:border-crt-green transition-all uppercase font-code shrink-0"
                  title="Open in the code editor"
                >
                  Open ⧉
                </button>
              )}
              <button
                onClick={() => { setSelectedPath(null); setPreview(null); }}
                className="text-[13px] w-5 h-5 flex items-center justify-center shrink-0 hover:opacity-100 transition-opacity"
                style={{ color: "var(--text-tertiary)", opacity: 0.7 }}
                title="Close inspector"
              >
                ×
              </button>
            </div>

            <div className="flex flex-col gap-1.5 px-3 py-2 shrink-0" style={{ borderBottom: "1px solid var(--border-color)" }}>
              {metaRow("path", selectedNode.path, selectedNode.path)}
              {selectedNode.type === "file"
                ? metaRow("size", fmtSize(selectedNode.size))
                : metaRow(
                    "inside",
                    selectedStats
                      ? `${selectedStats.files} file${selectedStats.files === 1 ? "" : "s"} · ${selectedStats.dirs} dir${selectedStats.dirs === 1 ? "" : "s"} · ${fmtSize(selectedStats.bytes)}`
                      : "—"
                  )}
              {selectedNode.cid && metaRow("cid", selectedNode.cid, selectedNode.cid)}
            </div>

            {selectedNode.type === "file" ? (
              <div className="flex-1 overflow-auto min-h-0">
                {preview?.path === selectedNode.path && preview.loading ? (
                  <div className="p-3 text-[10.5px] font-code" style={{ color: "var(--text-tertiary)" }}>loading…</div>
                ) : preview?.path === selectedNode.path && preview.error ? (
                  <div className="p-3 text-[10.5px] font-code" style={{ color: "var(--crt-amber)" }}>⚠ {preview.error}</div>
                ) : preview?.path === selectedNode.path ? (
                  <>
                    <pre
                      className="px-3 py-2 text-[10px] leading-[1.55] font-code whitespace-pre"
                      style={{ color: "var(--text-secondary)", tabSize: 2 }}
                    >
                      {preview.text || "(empty file)"}
                    </pre>
                    {(selectedNode.size || 0) > PREVIEW_MAX_CHARS && (
                      <div className="px-3 pb-2 text-[9.5px] font-code" style={{ color: "var(--text-tertiary)" }}>
                        preview truncated — open in editor for the full file
                      </div>
                    )}
                  </>
                ) : (
                  <div className="p-3 text-[10.5px] font-code" style={{ color: "var(--text-tertiary)" }}>no preview</div>
                )}
              </div>
            ) : (
              <div className="flex-1 overflow-auto min-h-0 py-1">
                {loadingDirs.has(selectedNode.path) ? (
                  <div className="p-3 text-[10.5px] font-code" style={{ color: "var(--text-tertiary)" }}>loading contents…</div>
                ) : (selectedNode.children || []).length === 0 ? (
                  <div className="p-3 flex flex-col gap-2">
                    <span className="text-[10.5px] font-code" style={{ color: "var(--text-tertiary)" }}>
                      {grafts.has(selectedNode.path) || !loadChildren ? "empty directory" : "contents not loaded yet"}
                    </span>
                    {!grafts.has(selectedNode.path) && loadChildren && (
                      <button
                        onClick={() => fetchDir(selectedNode.path)}
                        className="self-start text-[10px] px-2 h-6 border border-crt-amber/25 text-crt-amber/60 hover:text-crt-amber hover:border-crt-amber/60 transition-all uppercase font-code"
                      >
                        Load contents
                      </button>
                    )}
                  </div>
                ) : (
                  (selectedNode.children || []).map((c) => (
                    <button
                      key={c.path}
                      onClick={() => {
                        if (c.type === "directory") {
                          setExpanded((prev) => new Set(prev).add(selectedNode.path));
                          clickNode({ path: c.path, isDir: true, kidsKnown: (c.children?.length || 0) > 0 || grafts.has(c.path) });
                          // Selecting from the list shouldn't collapse an
                          // already-open dir — force it open instead.
                          setExpanded((prev) => new Set(prev).add(c.path));
                        } else {
                          setSelectedPath(c.path);
                          previewFile(c.path);
                        }
                      }}
                      className="w-full flex items-center gap-2 px-3 py-[3px] text-left hover:bg-white/[0.04] transition-colors"
                    >
                      {c.type === "directory" ? (
                        <span
                          className="inline-block rounded-[2px] shrink-0"
                          style={{ width: 7, height: 7, border: "1.2px solid var(--crt-amber)" }}
                        />
                      ) : (
                        <span
                          className="inline-block rounded-full shrink-0"
                          style={{ width: 7, height: 7, background: fileColor(c.name), boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.28)" }}
                        />
                      )}
                      <span className="text-[10.5px] font-code truncate flex-1 min-w-0" style={{ color: "var(--text-secondary)", fontWeight: c.type === "directory" ? 700 : 400 }}>
                        {c.name}
                      </span>
                      <span className="text-[9.5px] font-code shrink-0" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                        {c.type === "directory" ? `${subtreeStats(c).files || "·"}` : fmtSize(c.size)}
                      </span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Legend — redundant color key for the file-type marks. */}
      {legend.length > 0 && (
        <div
          className="flex items-center gap-3 px-3 py-1.5 shrink-0 flex-wrap"
          style={{ borderTop: "1px solid var(--border-color)" }}
        >
          <span className="flex items-center gap-1.5 text-[10px] font-code" style={{ color: "var(--text-tertiary)" }}>
            <span
              className="inline-block rounded-[3px]"
              style={{ width: 8, height: 8, border: "1.2px solid var(--crt-amber)", background: "color-mix(in srgb, var(--crt-amber) 26%, transparent)" }}
            />
            folder
          </span>
          {legend.map(([ext, v]) => (
            <span key={ext} className="flex items-center gap-1.5 text-[10px] font-code" style={{ color: "var(--text-tertiary)" }}>
              <span
                className="inline-block rounded-full"
                style={{ width: 8, height: 8, background: v.color, boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.28)" }}
              />
              {ext} ({v.n})
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
