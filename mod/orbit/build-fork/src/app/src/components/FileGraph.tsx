"use client";

// FileGraph — the CODE tab's "Graph" sub-view: the module's file tree drawn
// as a diagram for people who parse structure visually faster than an indented
// list. Same data as the Files list (/files/tree), same per-type identity
// colors, so a .rs file is the same hue in every view.
//
// One tree, five ways of drawing it (the STYLE pills):
//   TREE      horizontal node-link chips — the shape of the hierarchy
//   RADIAL    the same links wrapped around the root — depth reads as distance
//   ICICLE    stacked bars, width ∝ bytes — where the weight sits, by level
//   SUNBURST  the icicle in polar coordinates — angle ∝ bytes
//   TREEMAP   nested boxes, area ∝ bytes — the heaviest files, biggest first
// The node-link pair answers "how is this organised?", the area trio answers
// "what is this made of?". Everything else — expansion, selection, hover,
// zoom, the inspector, the legend — is shared, so switching style never loses
// your place.
//
// The /files/tree payload is depth-limited, so deep directories arrive with
// no children — clicking one lazy-loads its subtree via loadChildren and
// grafts it in (grafts are kept locally so periodic tree refreshes don't
// stomp them). Clicking any node also opens the inspector panel on the
// right: files show size/CID plus a content preview (loadContent) with an
// "open in editor" escape hatch; directories show subtree stats and a
// clickable child list.

import { useEffect, useMemo, useRef, useState } from "react";

export interface FileGraphNode {
  name: string;
  path: string;
  type: "file" | "directory";
  cid?: string | null;
  size?: number | null;
  children?: FileGraphNode[];
}

// The visible tree: mergedTree filtered through `expanded`, so a collapsed
// directory is a leaf that still knows what it's hiding (fileCount/bytes).
// Every layout below is a pure function of this one structure.
interface VNode {
  name: string;
  path: string;
  isDir: boolean;
  isRoot: boolean;
  cid?: string | null;
  size?: number | null;
  depth: number;
  expanded: boolean;
  kidsKnown: boolean; // false = children never loaded (depth-truncated)
  fileCount: number; // files in the FULL subtree (collapsed-dir badge)
  bytes: number; // bytes in the full subtree
  weight: number; // area/angle weight — bytes, floored so nothing vanishes
  children: VNode[]; // visible children only
}

type StyleId = "tree" | "radial" | "icicle" | "sunburst" | "map";

const STYLES: Array<{ id: StyleId; label: string; hint: string }> = [
  { id: "tree", label: "Tree", hint: "click a folder to open/close it, a file to inspect it" },
  { id: "radial", label: "Radial", hint: "the same tree wrapped around its root — depth reads as distance out" },
  { id: "icicle", label: "Icicle", hint: "one row per level, width ∝ bytes on disk" },
  { id: "sunburst", label: "Sunburst", hint: "the icicle in a circle — angle ∝ bytes, rings are levels" },
  { id: "map", label: "Treemap", hint: "area ∝ bytes on disk — the biggest boxes are the heaviest files" },
];
const STYLE_KEY = "buildfork_graph_style";

const NODE_H = 28; // chip height
const ROW_H = 36; // vertical distance between leaf rows
const COL_GAP = 54; // horizontal gap between a column's widest chip and the next
const PAD_X = 20;
const PAD_Y = 18;
const FONT_SIZE = 12.5;
const CHAR_W = 7.6; // monospace advance at FONT_SIZE, plus a hair of slack
const CHIP_PAD = 11; // chip inner padding
const MARK_W = 17; // mark + gap before the label, inside the chip
const LABEL_MAX = 26; // truncate labels beyond this many chars
// Zoom range. The floor is low enough that "expand all" on a real module
// still fits on screen — at that size the labels are unreadable, but the
// shape of the tree is the thing you're looking at.
const MIN_SCALE = 0.15;
const MAX_SCALE = 2;
const ZOOM_STEP = 1.2; // multiplicative, so each click feels the same either way
const AUTO_EXPAND_BUDGET = 140; // initial auto-expand stops near this many visible nodes
const PREVIEW_MAX_CHARS = 60_000; // inspector preview cap — full file lives in the editor

// Radial
const RADIAL_MIN_RING = 88; // shortest depth-to-depth radius step
const RADIAL_ARC_PER_LEAF = 15; // outer-ring px per leaf, so labels get room
const RADIAL_LABEL_ROOM = 150; // canvas margin for the outermost labels
// Icicle / sunburst — rows and rings stretch to fill the pane, within bounds:
// tall enough to read, short enough that a deep tree still fits.
const ICICLE_ROW_MIN = 22;
const ICICLE_ROW_MAX = 72;
const SUNBURST_RING_MIN = 18;
const SUNBURST_RING_MAX = 96;
// Area-layout typography — smaller than the chips, since cells are tight.
const CELL_FONT = 10.5;
const CELL_CHAR_W = 6.25;
const TREEMAP_HEADER = 15; // label strip at the top of an open treemap dir
const TREEMAP_PAD = 2;

const ROOT_PATH = "$graph-root";
const TAU = Math.PI * 2;

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

// Chip width from its rendered text — monospace, so a char count is exact
// enough and needs no DOM measurement.
function chipWidth(label: string, badge: string): number {
  return CHIP_PAD * 2 + MARK_W + (label.length + badge.length) * CHAR_W;
}

// Longest prefix of `name` that fits in `px`, or "" if even two chars don't.
function fitLabel(name: string, px: number): string {
  const max = Math.floor(px / CELL_CHAR_W);
  if (max < 2) return "";
  return name.length <= max ? name : name.slice(0, Math.max(1, max - 1)) + "…";
}

function polar(cx: number, cy: number, r: number, a: number): [number, number] {
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

// Annulus wedge. r0 ≈ 0 degenerates to a pie slice; a full turn is drawn by
// the caller as a plain circle (SVG arcs can't close a 360° sweep).
function arcPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = polar(cx, cy, r1, a0);
  const [x1, y1] = polar(cx, cy, r1, a1);
  if (r0 <= 0.01) return `M ${cx} ${cy} L ${x0} ${y0} A ${r1} ${r1} 0 ${large} 1 ${x1} ${y1} Z`;
  const [x2, y2] = polar(cx, cy, r0, a1);
  const [x3, y3] = polar(cx, cy, r0, a0);
  return `M ${x0} ${y0} A ${r1} ${r1} 0 ${large} 1 ${x1} ${y1} L ${x2} ${y2} A ${r0} ${r0} 0 ${large} 0 ${x3} ${y3} Z`;
}

// ── layouts ───────────────────────────────────────────────────────────────
// Each takes the visible tree and returns plain geometry; nothing here
// touches React, so a style switch is one recompute and no state churn.

interface Chip { v: VNode; x: number; y: number; w: number }
interface Link { from: { x: number; y: number; path: string }; to: { x: number; y: number; path: string } }

// TREE — columns are as wide as their widest chip, leaves stack by row, and a
// parent sits at the midpoint of its children.
function layoutTree(root: VNode): { chips: Chip[]; links: Link[]; width: number; height: number } {
  const chips: Chip[] = [];
  const links: Link[] = [];
  let leafRow = 0;
  let maxDepth = 0;

  const walk = (v: VNode): Chip => {
    maxDepth = Math.max(maxDepth, v.depth);
    const chip: Chip = { v, x: 0, y: 0, w: chipWidth(truncateLabel(v.name), badgeOf(v)) };
    if (v.children.length) {
      const kids = v.children.map(walk);
      chip.y = (kids[0].y + kids[kids.length - 1].y) / 2;
      for (const k of kids) links.push({ from: { x: 0, y: chip.y, path: v.path }, to: { x: 0, y: k.y, path: k.v.path } });
    } else {
      chip.y = PAD_Y + NODE_H / 2 + leafRow * ROW_H;
      leafRow++;
    }
    chips.push(chip);
    return chip;
  };
  walk(root);

  // Column widths, then a second pass to place x — links need both ends'
  // chip geometry, so they're finished from the chip index afterwards.
  const colW: number[] = [];
  for (const c of chips) colW[c.v.depth] = Math.max(colW[c.v.depth] || 0, c.w);
  const colX: number[] = [PAD_X];
  for (let d = 1; d <= maxDepth; d++) colX[d] = colX[d - 1] + (colW[d - 1] || 0) + COL_GAP;
  for (const c of chips) c.x = colX[c.v.depth];

  const byPath = new Map(chips.map((c) => [c.v.path, c]));
  for (const l of links) {
    const a = byPath.get(l.from.path)!;
    const b = byPath.get(l.to.path)!;
    l.from.x = a.x + a.w;
    l.to.x = b.x;
  }
  return {
    chips,
    links,
    width: colX[maxDepth] + (colW[maxDepth] || 0) + PAD_X,
    height: Math.max(PAD_Y * 2 + Math.max(leafRow, 1) * ROW_H, 120),
  };
}

interface RadialNode { v: VNode; x: number; y: number; a: number; r: number }

// RADIAL — leaves get equal angular slots, parents the mean of their kids;
// the ring step grows with the leaf count so the outer labels keep their air.
function layoutRadial(root: VNode, leafCount: number, maxDepth: number, availW: number, availH: number) {
  const ring = Math.max(RADIAL_MIN_RING, (leafCount * RADIAL_ARC_PER_LEAF) / TAU / Math.max(maxDepth, 1));
  const maxR = ring * maxDepth;
  const half = maxR + RADIAL_LABEL_ROOM;
  // Pad the canvas out to the pane so a small tree sits centred rather than
  // pinned to the top-left corner.
  const width = Math.max(half * 2, availW);
  const height = Math.max(half * 2, availH);
  const cx = width / 2;
  const cy = height / 2;
  const nodes: RadialNode[] = [];
  const links: Link[] = [];
  let leaf = 0;

  const walk = (v: VNode): RadialNode => {
    const r = v.depth * ring;
    let a: number;
    if (v.children.length) {
      const kids = v.children.map(walk);
      a = (kids[0].a + kids[kids.length - 1].a) / 2;
      const [px, py] = polar(cx, cy, r, a);
      for (const k of kids) {
        links.push({ from: { x: px, y: py, path: v.path }, to: { x: k.x, y: k.y, path: k.v.path } });
      }
    } else {
      a = ((leaf + 0.5) / Math.max(leafCount, 1)) * TAU - Math.PI / 2;
      leaf++;
    }
    const [x, y] = polar(cx, cy, r, a);
    const self: RadialNode = { v, x, y, a, r };
    nodes.push(self);
    return self;
  };
  walk(root);

  return { nodes, links, cx, cy, ring, width, height };
}

interface Cell { v: VNode; x: number; y: number; w: number; h: number }

// ICICLE — a horizontal partition: every level is a row, every node owns a
// slice of its parent's width in proportion to its weight.
function layoutIcicle(root: VNode, width: number, maxDepth: number, availH: number) {
  const rows = maxDepth + 1;
  const row = Math.max(ICICLE_ROW_MIN, Math.min(ICICLE_ROW_MAX, availH / rows));
  // A shallow tree can't fill the pane at a sane row height, so centre it
  // rather than pinning it to the top with a field of dead space below.
  const y0 = Math.max(0, (availH - rows * row) / 2);
  const cells: Cell[] = [];
  const walk = (v: VNode, x: number, w: number) => {
    cells.push({ v, x, y: y0 + v.depth * row, w, h: row });
    let cx = x;
    for (const c of v.children) {
      const cw = (c.weight / v.weight) * w;
      walk(c, cx, cw);
      cx += cw;
    }
  };
  walk(root, 0, width);
  return { cells, width, height: Math.max(rows * row, availH) };
}

interface Wedge { v: VNode; a0: number; a1: number; r0: number; r1: number }

// SUNBURST — the icicle in polar coordinates: width becomes angle, row
// becomes ring. Radius is capped so deep trees still fit the viewport.
function layoutSunburst(root: VNode, maxDepth: number, availW: number, availH: number) {
  const ring = Math.max(SUNBURST_RING_MIN, Math.min(SUNBURST_RING_MAX, (Math.min(availW, availH) - 24) / 2 / (maxDepth + 1)));
  const wedges: Wedge[] = [];
  const walk = (v: VNode, a0: number, a1: number) => {
    wedges.push({ v, a0, a1, r0: v.depth * ring, r1: (v.depth + 1) * ring });
    let a = a0;
    for (const c of v.children) {
      const span = (c.weight / v.weight) * (a1 - a0);
      walk(c, a, a + span);
      a += span;
    }
  };
  walk(root, -Math.PI / 2, -Math.PI / 2 + TAU);
  const size = (maxDepth + 1) * ring * 2 + 24;
  const width = Math.max(size, availW);
  const height = Math.max(size, availH);
  return { wedges, ring, cx: width / 2, cy: height / 2, width, height };
}

// Squarified treemap: fill the shorter side first, closing a band as soon as
// adding the next item would make its aspect ratios worse.
function worstRatio(areas: number[], total: number, short: number): number {
  let max = -Infinity;
  let min = Infinity;
  for (const a of areas) {
    if (a > max) max = a;
    if (a < min) min = a;
  }
  const s2 = short * short;
  const t2 = total * total;
  return Math.max((s2 * max) / t2, t2 / (s2 * min));
}

function squarify(
  items: VNode[],
  x: number,
  y: number,
  w: number,
  h: number,
  place: (v: VNode, x: number, y: number, w: number, h: number) => void
) {
  const total = items.reduce((a, i) => a + i.weight, 0);
  if (total <= 0 || w <= 0 || h <= 0) return;
  const unit = (w * h) / total;
  const rest = items.map((v) => ({ v, area: v.weight * unit })).sort((a, b) => b.area - a.area);

  while (rest.length) {
    const short = Math.min(w, h);
    if (short <= 0.5) return;
    const row: Array<{ v: VNode; area: number }> = [];
    let rowArea = 0;
    let best = Infinity;
    while (rest.length) {
      const cand = rest[0];
      const nextArea = rowArea + cand.area;
      const ratio = worstRatio([...row.map((r) => r.area), cand.area], nextArea, short);
      if (row.length && ratio > best) break;
      row.push(rest.shift()!);
      rowArea = nextArea;
      best = ratio;
    }
    const thick = rowArea / short;
    if (w >= h) {
      let cy = y;
      for (const r of row) {
        const rh = r.area / thick;
        place(r.v, x, cy, thick, rh);
        cy += rh;
      }
      x += thick;
      w -= thick;
    } else {
      let cx = x;
      for (const r of row) {
        const rw = r.area / thick;
        place(r.v, cx, y, rw, thick);
        cx += rw;
      }
      y += thick;
      h -= thick;
    }
  }
}

// TREEMAP — nested squarified boxes. Parents are pushed before their children
// so the paint order puts kids on top of the frame that holds them.
function layoutTreemap(root: VNode, width: number, height: number) {
  const cells: Cell[] = [];
  const place = (v: VNode, x: number, y: number, w: number, h: number) => {
    cells.push({ v, x, y, w, h });
    if (!v.children.length || w < 12 || h < 12) return;
    const header = h > TREEMAP_HEADER + 14 ? TREEMAP_HEADER : 0;
    squarify(
      v.children,
      x + TREEMAP_PAD,
      y + header,
      w - TREEMAP_PAD * 2,
      h - header - TREEMAP_PAD,
      place
    );
  };
  place(root, 0, 0, width, height);
  return { cells, width, height };
}

function badgeOf(v: VNode): string {
  if (!v.isDir || v.expanded || v.isRoot) return "";
  if (!v.kidsKnown) return " ＋";
  return v.fileCount > 0 ? ` ${v.fileCount}` : "";
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
  const [style, setStyle] = useState<StyleId>("tree");
  const [scale, setScale] = useState(1);
  const [hover, setHover] = useState<{ v: VNode; x: number; y: number } | null>(null);
  // Lazily-fetched subtrees keyed by dir path — kept out of the parent's tree
  // state so its periodic refreshes can't wipe them.
  const [grafts, setGrafts] = useState<Map<string, FileGraphNode[]>>(new Map());
  const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ path: string; text: string; loading: boolean; error?: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  // The area layouts have no intrinsic size — they fill whatever the scroller
  // gives them, so a fresh style opens fitted instead of needing a zoom.
  const [viewport, setViewport] = useState({ w: 900, h: 560 });
  const scrollRef = useRef<HTMLDivElement>(null);

  // Style is a viewing preference, so it outlives the session.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STYLE_KEY) as StyleId | null;
      if (saved && STYLES.some((s) => s.id === saved)) setStyle(saved);
    } catch {}
  }, []);
  const pickStyle = (id: StyleId) => {
    setStyle(id);
    setScale(1);
    setHover(null);
    try {
      localStorage.setItem(STYLE_KEY, id);
    } catch {}
  };

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

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setViewport({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setViewport({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

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

  // The visible tree — the single input every layout reads.
  const { vroot, dirCount, fileCount, leafCount, maxDepth } = useMemo(() => {
    let dirs = 0;
    let files = 0;
    let leaves = 0;
    let deepest = 0;

    const build = (n: FileGraphNode, depth: number): VNode => {
      const isDir = n.type === "directory";
      if (isDir) dirs++;
      else files++;
      deepest = Math.max(deepest, depth);
      const kids = n.children || [];
      const isOpen = isDir && kids.length > 0 && expanded.has(n.path);
      const stats = isDir ? subtreeStats(n) : null;
      const children = isOpen ? kids.map((c) => build(c, depth + 1)) : [];
      if (!children.length) leaves++;
      // Weight drives every area layout. A file is worth at least 1 so empty
      // files still get a sliver; a collapsed dir falls back to its file count
      // when its contents are all zero-byte.
      const weight = children.length
        ? children.reduce((a, c) => a + c.weight, 0)
        : isDir
          ? Math.max(stats!.bytes, stats!.files, 1)
          : Math.max(n.size || 0, 1);
      return {
        name: n.name,
        path: n.path,
        isDir,
        isRoot: false,
        cid: n.cid,
        size: n.size,
        depth,
        expanded: isOpen,
        kidsKnown: !isDir || kids.length > 0 || grafts.has(n.path),
        fileCount: stats ? stats.files : 0,
        bytes: stats ? stats.bytes : n.size || 0,
        weight,
        children,
      };
    };

    // Synthetic root: the browsed directory itself, always open.
    const children = mergedTree.map((c) => build(c, 1));
    if (!children.length) leaves++;
    const root: VNode = {
      name: rootLabel,
      path: ROOT_PATH,
      isDir: true,
      isRoot: true,
      depth: 0,
      expanded: true,
      kidsKnown: true,
      fileCount: children.reduce((a, c) => a + (c.isDir ? c.fileCount : 1), 0),
      bytes: children.reduce((a, c) => a + c.bytes, 0),
      weight: Math.max(children.reduce((a, c) => a + c.weight, 0), 1),
      children,
    };
    return { vroot: root, dirCount: dirs, fileCount: files, leafCount: leaves, maxDepth: deepest };
  }, [mergedTree, expanded, grafts, rootLabel]);

  // Geometry for the active style. Area layouts fill the scroller, so their
  // "natural" size is the viewport and zoom is purely additive on top.
  const geom = useMemo(() => {
    const availW = Math.max(320, viewport.w - 2);
    const availH = Math.max(240, viewport.h - 2);
    if (style === "tree") return { kind: "tree" as const, ...layoutTree(vroot) };
    if (style === "radial") return { kind: "radial" as const, ...layoutRadial(vroot, leafCount, maxDepth, availW, availH) };
    if (style === "icicle") return { kind: "icicle" as const, ...layoutIcicle(vroot, availW, maxDepth, availH) };
    if (style === "sunburst") return { kind: "sunburst" as const, ...layoutSunburst(vroot, maxDepth, availW, availH) };
    return { kind: "map" as const, ...layoutTreemap(vroot, availW, availH) };
  }, [style, vroot, leafCount, maxDepth, viewport]);

  const width = geom.width;
  const height = geom.height;

  // Zoom is multiplicative, so a click out undoes a click in exactly.
  const clampScale = (s: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, +s.toFixed(3)));
  const zoomBy = (factor: number) => setScale((s) => clampScale(s * factor));
  // FIT — the scale that puts the whole canvas inside the scroller, which is
  // what you want after "expand all" on anything bigger than a toy module.
  const fitToView = () => {
    const el = scrollRef.current;
    if (!el) return;
    setScale(clampScale(Math.min(el.clientWidth / width, el.clientHeight / height)));
  };

  // ⌘/ctrl + wheel — and trackpad pinch, which browsers report as exactly
  // that — zooms instead of scrolling. Attached natively because React's
  // synthetic wheel handler is passive, so preventDefault there is a no-op.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setScale((s) => clampScale(s * Math.exp(-e.deltaY / 300)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeKey]);

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

  // Per-node identity color: green root, amber folders, linguist hue for files.
  const colorOf = (v: VNode) => (v.isRoot ? "var(--crt-green)" : v.isDir ? "var(--crt-amber)" : fileColor(v.name));

  // Shared node props: one hit target, one hover source, one click path.
  const nodeHandlers = (v: VNode, tipX: number, tipY: number) => ({
    onClick: () => (v.isRoot ? undefined : clickNode(v)),
    onMouseEnter: () => setHover({ v, x: tipX, y: tipY }),
    onMouseLeave: () => setHover((h) => (h?.v.path === v.path ? null : h)),
    style: { cursor: v.isRoot ? "default" : "pointer" } as const,
  });

  const isLit = (v: VNode) => hover?.v.path === v.path || selectedPath === v.path;

  // A filled cell (icicle / treemap / sunburst share this surface treatment):
  // tinted by identity, lifted on hover, ringed when selected.
  const cellStyle = (v: VNode) => {
    const color = colorOf(v);
    const active = isLit(v);
    const base = v.isDir ? (v.isRoot ? 16 : 12) : 34;
    return {
      fill: `color-mix(in srgb, ${color} ${active ? base + 22 : base}%, var(--bg-primary))`,
      stroke:
        selectedPath === v.path
          ? "var(--crt-green)"
          : `color-mix(in srgb, ${color} ${hover?.v.path === v.path ? 85 : 40}%, transparent)`,
      strokeWidth: selectedPath === v.path ? 1.6 : 0.8,
      opacity: loadingDirs.has(v.path) ? 0.55 : 1,
      transition: "fill 120ms ease, stroke 120ms ease",
    };
  };

  const cellTextFill = (v: VNode) =>
    v.isRoot ? "var(--crt-green)" : v.isDir || isLit(v) ? "var(--text-primary)" : "var(--text-secondary)";

  const styleHint = STYLES.find((s) => s.id === style)?.hint || "";

  return (
    <div className="flex-1 flex flex-col overflow-hidden min-h-0">
      {/* Controls row — style, zoom, expansion state and subtree stats. */}
      <div
        className="flex items-center gap-1.5 px-3 py-1.5 shrink-0 flex-wrap"
        style={{ borderBottom: "1px solid var(--border-color)" }}
      >
        <span className="text-[11px] uppercase font-code tracking-[0.12em]" style={{ color: "var(--text-tertiary)" }}>
          {fileCount} files · {dirCount} dirs
        </span>
        <div className="flex items-center" style={{ border: "1px solid var(--border-color)" }}>
          {STYLES.map((s) => (
            <button
              key={s.id}
              onClick={() => pickStyle(s.id)}
              title={s.hint}
              className="text-[10px] px-2 h-6 uppercase font-code transition-all"
              style={{
                color: style === s.id ? "var(--crt-green)" : "var(--text-tertiary)",
                background: style === s.id ? "color-mix(in srgb, var(--crt-green) 12%, transparent)" : "transparent",
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
        <span className="text-[11px] font-code hidden lg:inline truncate" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
          — {styleHint}
        </span>
        <div className="flex-1" />
        <button onClick={expandAll} className="text-[10px] px-2 h-6 border border-crt-amber/25 text-crt-amber/60 hover:text-crt-amber hover:border-crt-amber/60 transition-all uppercase font-code">
          Expand all
        </button>
        <button onClick={() => setExpanded(initialExpanded(mergedTree))} className="text-[10px] px-2 h-6 border border-crt-amber/25 text-crt-amber/60 hover:text-crt-amber hover:border-crt-amber/60 transition-all uppercase font-code">
          Reset
        </button>
        <button onClick={() => zoomBy(1 / ZOOM_STEP)} className={zoomBtnCls} title="Zoom out (⌘/ctrl + scroll)">−</button>
        <button
          onClick={fitToView}
          className="text-[10px] px-2 h-6 border border-crt-green/20 text-crt-green/50 hover:text-crt-green hover:border-crt-green/50 transition-all uppercase font-code"
          title="Fit the whole graph in view"
        >
          Fit
        </button>
        <span className="text-[10px] font-code w-9 text-center" style={{ color: "var(--text-tertiary)" }}>
          {Math.round(scale * 100)}%
        </span>
        <button onClick={() => zoomBy(ZOOM_STEP)} className={zoomBtnCls} title="Zoom in (⌘/ctrl + scroll)">+</button>
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
              {geom.kind === "tree" && (
                <>
                  {/* Edges first (recessive hairlines under the chips), running
                      from a parent's right edge to its child's left edge. */}
                  {geom.links.map((e) => {
                    const midX = (e.from.x + e.to.x) / 2;
                    const lit = hover && (hover.v.path === e.to.path || hover.v.path === e.from.path);
                    return (
                      <path
                        key={`${e.from.path}→${e.to.path}`}
                        d={`M ${e.from.x} ${e.from.y} C ${midX} ${e.from.y}, ${midX} ${e.to.y}, ${e.to.x} ${e.to.y}`}
                        fill="none"
                        stroke={lit ? "color-mix(in srgb, var(--crt-green) 55%, transparent)" : "var(--border-color)"}
                        strokeWidth={lit ? 1.5 : 1}
                      />
                    );
                  })}
                  {geom.chips.map(({ v, x, y, w }) => {
                    const color = colorOf(v);
                    const active = isLit(v);
                    const badge = badgeOf(v);
                    return (
                      <g key={v.path} transform={`translate(${x}, ${y - NODE_H / 2})`} {...nodeHandlers(v, x + 12, y + NODE_H / 2 + 8)}>
                        {/* The chip — hit target, tint and label holder in one. */}
                        <rect
                          x={0}
                          y={0}
                          width={w}
                          height={NODE_H}
                          rx={8}
                          style={{
                            fill: `color-mix(in srgb, ${color} ${active ? 24 : 13}%, var(--bg-primary))`,
                            stroke:
                              selectedPath === v.path
                                ? "var(--crt-green)"
                                : `color-mix(in srgb, ${color} ${hover?.v.path === v.path ? 80 : 42}%, transparent)`,
                            strokeWidth: selectedPath === v.path ? 1.6 : 1,
                            opacity: loadingDirs.has(v.path) ? 0.55 : 1,
                            transition: "fill 120ms ease, stroke 120ms ease",
                          }}
                        />
                        {v.isDir ? (
                          <rect
                            x={CHIP_PAD}
                            y={NODE_H / 2 - 5}
                            width={10}
                            height={10}
                            rx={2.5}
                            style={{
                              fill: v.expanded || v.isRoot ? `color-mix(in srgb, ${color} 55%, transparent)` : "transparent",
                              stroke: color,
                              strokeWidth: 1.3,
                            }}
                          />
                        ) : (
                          <circle
                            cx={CHIP_PAD + 5}
                            cy={NODE_H / 2}
                            r={5}
                            style={{
                              fill: color,
                              // White rim keeps dark linguist hues visible on
                              // the near-black surface.
                              stroke: "rgba(255,255,255,0.28)",
                              strokeWidth: 1,
                            }}
                          />
                        )}
                        <text
                          x={CHIP_PAD + MARK_W}
                          y={NODE_H / 2}
                          dominantBaseline="central"
                          style={{
                            fontFamily: "var(--font-code, monospace)",
                            fontSize: FONT_SIZE,
                            fontWeight: v.isDir ? 700 : 500,
                            fill: cellTextFill(v),
                          }}
                        >
                          {truncateLabel(v.name)}
                          {badge && (
                            <tspan style={{ fill: "var(--text-tertiary)", fontWeight: 400 }}>
                              {loadingDirs.has(v.path) ? " ⋯" : badge}
                            </tspan>
                          )}
                        </text>
                      </g>
                    );
                  })}
                </>
              )}

              {geom.kind === "radial" && (
                <>
                  {geom.links.map((e) => {
                    const lit = hover && (hover.v.path === e.to.path || hover.v.path === e.from.path);
                    // Elbow through the parent's ring, so siblings share a
                    // visible spine instead of a fan of straight spokes.
                    const mx = (e.from.x + e.to.x) / 2;
                    const my = (e.from.y + e.to.y) / 2;
                    return (
                      <path
                        key={`${e.from.path}→${e.to.path}`}
                        d={`M ${e.from.x} ${e.from.y} Q ${mx + (e.from.x - geom.cx) * 0.12} ${my + (e.from.y - geom.cy) * 0.12} ${e.to.x} ${e.to.y}`}
                        fill="none"
                        stroke={lit ? "color-mix(in srgb, var(--crt-green) 55%, transparent)" : "var(--border-color)"}
                        strokeWidth={lit ? 1.5 : 1}
                      />
                    );
                  })}
                  {geom.nodes.map(({ v, x, y, a }) => {
                    const color = colorOf(v);
                    const active = isLit(v);
                    const deg = (a * 180) / Math.PI;
                    const flip = deg > 90 || deg < -90;
                    const label = truncateLabel(v.name) + badgeOf(v);
                    return (
                      <g key={v.path} {...nodeHandlers(v, x + 10, y + 12)}>
                        {/* Generous invisible hit area — the visible mark is
                            small, the target shouldn't be. */}
                        <circle cx={x} cy={y} r={11} fill="transparent" />
                        {v.isDir ? (
                          <rect
                            x={x - 5}
                            y={y - 5}
                            width={10}
                            height={10}
                            rx={2.5}
                            style={{
                              fill: v.expanded ? `color-mix(in srgb, ${color} 55%, transparent)` : "var(--bg-primary)",
                              stroke: color,
                              strokeWidth: active ? 1.8 : 1.3,
                            }}
                          />
                        ) : (
                          <circle
                            cx={x}
                            cy={y}
                            r={active ? 5.5 : 4.2}
                            style={{ fill: color, stroke: "rgba(255,255,255,0.28)", strokeWidth: 1 }}
                          />
                        )}
                        <text
                          transform={`translate(${x}, ${y}) rotate(${flip ? deg + 180 : deg}) translate(${flip ? -10 : 10}, 0)`}
                          textAnchor={flip ? "end" : "start"}
                          dominantBaseline="central"
                          style={{
                            fontFamily: "var(--font-code, monospace)",
                            fontSize: v.isRoot ? FONT_SIZE : 11,
                            fontWeight: v.isDir ? 700 : 500,
                            fill: cellTextFill(v),
                          }}
                        >
                          {label}
                        </text>
                      </g>
                    );
                  })}
                </>
              )}

              {geom.kind === "icicle" &&
                geom.cells.map(({ v, x, y, w, h }) => {
                  const label = fitLabel(v.name + badgeOf(v), w - 10);
                  return (
                    <g key={v.path} {...nodeHandlers(v, x + 8, y + h + 6)}>
                      <rect x={x + 0.5} y={y + 0.5} width={Math.max(w - 1, 0.5)} height={h - 1.5} rx={2} style={cellStyle(v)} />
                      {label && w > 16 && (
                        <text
                          x={x + 5}
                          y={y + h / 2}
                          dominantBaseline="central"
                          style={{
                            fontFamily: "var(--font-code, monospace)",
                            fontSize: CELL_FONT,
                            fontWeight: v.isDir ? 700 : 400,
                            fill: cellTextFill(v),
                            pointerEvents: "none",
                          }}
                        >
                          {label}
                        </text>
                      )}
                    </g>
                  );
                })}

              {geom.kind === "sunburst" &&
                geom.wedges.map(({ v, a0, a1, r0, r1 }) => {
                  const mid = (a0 + a1) / 2;
                  const [tx, ty] = polar(geom.cx, geom.cy, (r0 + r1) / 2, mid);
                  // The label runs outward along the ray, so the arc has to be
                  // tall enough for a glyph and the ring wide enough for the
                  // word — two different measurements.
                  const arcPx = (a1 - a0) * ((r0 + r1) / 2);
                  const label = arcPx > CELL_FONT + 2 ? fitLabel(v.name, r1 - r0 - 8) : "";
                  const deg = (mid * 180) / Math.PI;
                  const flip = deg > 90 || deg < -90;
                  return (
                    <g key={v.path} {...nodeHandlers(v, tx + 8, ty + 10)}>
                      {v.isRoot ? (
                        <circle cx={geom.cx} cy={geom.cy} r={geom.ring} style={cellStyle(v)} />
                      ) : (
                        <path d={arcPath(geom.cx, geom.cy, r0 + 0.5, r1 - 0.5, a0, a1 - 0.002)} style={cellStyle(v)} />
                      )}
                      {(v.isRoot || label) && (
                        <text
                          transform={
                            v.isRoot
                              ? `translate(${geom.cx}, ${geom.cy})`
                              : `translate(${tx}, ${ty}) rotate(${flip ? deg + 180 : deg})`
                          }
                          textAnchor="middle"
                          dominantBaseline="central"
                          style={{
                            fontFamily: "var(--font-code, monospace)",
                            fontSize: CELL_FONT,
                            fontWeight: v.isDir ? 700 : 400,
                            fill: cellTextFill(v),
                            pointerEvents: "none",
                          }}
                        >
                          {v.isRoot ? fitLabel(v.name, geom.ring * 1.7) : label}
                        </text>
                      )}
                    </g>
                  );
                })}

              {geom.kind === "map" &&
                geom.cells.map(({ v, x, y, w, h }) => {
                  if (w < 1.2 || h < 1.2) return null;
                  // An open dir labels its header strip; a leaf labels its
                  // middle, and only when the box can hold the text.
                  const open = v.children.length > 0 && h > TREEMAP_HEADER + 14 && w > 24;
                  const label = open
                    ? fitLabel(v.name, w - 8)
                    : w > 26 && h > 14
                      ? fitLabel(v.name + badgeOf(v), w - 8)
                      : "";
                  return (
                    <g key={v.path} {...nodeHandlers(v, x + 8, y + Math.min(h, 24) + 6)}>
                      <rect x={x + 0.5} y={y + 0.5} width={Math.max(w - 1, 0.5)} height={Math.max(h - 1, 0.5)} rx={2} style={cellStyle(v)} />
                      {label && (
                        <text
                          x={x + 4}
                          y={open ? y + TREEMAP_HEADER / 2 + 1 : y + h / 2}
                          dominantBaseline="central"
                          style={{
                            fontFamily: "var(--font-code, monospace)",
                            fontSize: CELL_FONT,
                            fontWeight: v.isDir ? 700 : 400,
                            fill: cellTextFill(v),
                            pointerEvents: "none",
                          }}
                        >
                          {label}
                        </text>
                      )}
                    </g>
                  );
                })}
            </svg>

            {/* Hover tooltip — full path (labels truncate) + type/size + CID. */}
            {hover && !hover.v.isRoot && (
              <div
                className="absolute pointer-events-none px-2.5 py-1.5 rounded-md font-code text-[11.5px] leading-snug z-10"
                style={{
                  left: Math.min(hover.x * scale, Math.max(0, width * scale - 260)),
                  top: hover.y * scale,
                  maxWidth: 280,
                  background: "color-mix(in srgb, var(--bg-primary) 92%, transparent)",
                  border: "1px solid var(--border-color-strong, var(--border-color))",
                  color: "var(--text-secondary)",
                  backdropFilter: "blur(6px)",
                }}
              >
                <div className="break-all" style={{ color: "var(--text-primary)" }}>{hover.v.path}</div>
                <div style={{ color: "var(--text-tertiary)" }}>
                  {hover.v.isDir
                    ? loadingDirs.has(hover.v.path)
                      ? "loading contents…"
                      : !hover.v.kidsKnown
                        ? "not loaded — click to load"
                        : `${hover.v.fileCount} file${hover.v.fileCount === 1 ? "" : "s"} · ${fmtSize(hover.v.bytes)} — click to ${hover.v.expanded ? "collapse" : "expand"}`
                    : `${fmtSize(hover.v.size)} — click to inspect`}
                  {hover.v.cid ? ` · ${hover.v.cid.slice(0, 10)}` : ""}
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
          <span className="flex items-center gap-1.5 text-[11px] font-code" style={{ color: "var(--text-tertiary)" }}>
            <span
              className="inline-block rounded-[3px]"
              style={{ width: 8, height: 8, border: "1.2px solid var(--crt-amber)", background: "color-mix(in srgb, var(--crt-amber) 26%, transparent)" }}
            />
            folder
          </span>
          {legend.map(([ext, v]) => (
            <span key={ext} className="flex items-center gap-1.5 text-[11px] font-code" style={{ color: "var(--text-tertiary)" }}>
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
