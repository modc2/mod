"use client";

// Desk — a browser-OS window manager for module apps, docked under the
// console header (it splits the mod views; the header stays on top).
// Each window iframes a
// gateway /{mod} app. Drag a titlebar to the screen edges for Windows-style
// snap zones (halves at the sides, quarters at the corners, maximize at the
// top), pick a cell from the ⊞ snap-layout popup, or use snap-assist: after
// snapping to a half, the empty half offers the other open windows so a
// split screen is two clicks. Windows stay mounted while the Desk is closed
// so embedded apps keep their state. The ⧉ titlebar button opens ANOTHER
// instance of the same app on its own storage profile (?profile= — see
// layout.tsx's shim), so two panes can be signed into different accounts.

import React, { useCallback, useEffect, useRef, useState } from "react";

type Frac = { x: number; y: number; w: number; h: number };
type Rect = { x: number; y: number; w: number; h: number };
type Win = {
  id: string;
  mod: string;
  // Storage profile for this instance (see layout.tsx's ?profile= shim):
  // undefined = the default shared session; "2"/"3"/… get their own
  // namespaced web storage, so each window can sign into a different
  // account. Two windows of the same mod differ only by profile.
  profile?: string;
  rect: Rect; // floating position/size in px (used when snap === null)
  snap: Frac | null; // fraction-of-desk rect when snapped ({0,0,1,1} = maximized)
  restore: { w: number; h: number } | null; // floating size to restore on unsnap
  z: number;
  min: boolean;
  gen: number; // bump to reload the iframe
};

const TASKBAR_H = 40;
const TITLE_H = 30;
const GAP = 5; // breathing room between snapped windows
const MIN_W = 260;
const MIN_H = 180;
const EDGE = 20; // px from a desk edge that arms a snap zone while dragging

const ZONES = {
  left: { x: 0, y: 0, w: 0.5, h: 1 },
  right: { x: 0.5, y: 0, w: 0.5, h: 1 },
  bottom: { x: 0, y: 0.5, w: 1, h: 0.5 },
  max: { x: 0, y: 0, w: 1, h: 1 },
  tl: { x: 0, y: 0, w: 0.5, h: 0.5 },
  tr: { x: 0.5, y: 0, w: 0.5, h: 0.5 },
  bl: { x: 0, y: 0.5, w: 0.5, h: 0.5 },
  br: { x: 0.5, y: 0.5, w: 0.5, h: 0.5 },
} as const;

// Win11-style snap layouts for the ⊞ popup: each is a set of cells; clicking
// a cell sends the window there.
const LAYOUTS: Frac[][] = [
  [ZONES.left, ZONES.right],
  [{ x: 0, y: 0, w: 1 / 3, h: 1 }, { x: 1 / 3, y: 0, w: 1 / 3, h: 1 }, { x: 2 / 3, y: 0, w: 1 / 3, h: 1 }],
  [{ x: 0, y: 0, w: 2 / 3, h: 1 }, { x: 2 / 3, y: 0, w: 1 / 3, h: 1 }],
  [ZONES.tl, ZONES.tr, ZONES.bl, ZONES.br],
  [{ x: 0, y: 0, w: 1, h: 0.5 }, ZONES.bottom],
  [ZONES.max],
];

// Window ids are mod + profile so openWin stays idempotent per instance.
const winId = (mod: string, profile?: string) => (profile ? `w-${mod}~${profile}` : `w-${mod}`);

// iframe URL for a window — a profile rides along as ?profile= so the app's
// storage shim (layout.tsx) gives that pane its own isolated session.
const winSrc = (base: string, mod: string, profile?: string) =>
  `${base}/${mod}${profile ? `?profile=${encodeURIComponent(profile)}` : ""}`;

const sameFrac = (a: Frac | null, b: Frac | null) =>
  !!a && !!b && Math.abs(a.x - b.x) < 0.001 && Math.abs(a.y - b.y) < 0.001 &&
  Math.abs(a.w - b.w) < 0.001 && Math.abs(a.h - b.h) < 0.001;

const STORE_KEY = "buildfork.desk.v1";

type DragState = {
  id: string;
  mode: "move" | "resize";
  edges: string; // for resize: some of "n s e w"
  startX: number;
  startY: number;
  orig: Rect;
  moved: boolean;
};

export default function Desk({
  open,
  onClose,
  origin,
  mods,
  launch,
  tasks,
  onEdit,
}: {
  open: boolean;
  onClose: () => void;
  origin: string;
  mods: string[];
  // cells: preset split-screen layout (up to 4 panes) — launch.mod snaps
  // into cells[0], the rest render as "pick an app" slots. dual: cells[1]
  // gets a SECOND instance of the same mod on its own storage profile, so
  // the two panes can be signed into different accounts.
  launch: { mod: string; n: number; cells?: Frac[]; dual?: boolean } | null;
  // Per-module task counts (console's railTasks.byModule) — drives the
  // titlebar badges and the finished-task iframe reload.
  tasks?: Record<string, { running: number; pending: number }>;
  // Submit an edit task for a module from its window's ✎ bar.
  // Resolves to an error string (shown inline) or null on success.
  onEdit?: (mod: string, prompt: string) => Promise<string | null>;
}) {
  const [wins, setWins] = useState<Win[]>([]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [preview, setPreview] = useState<Frac | null>(null);
  const [interacting, setInteracting] = useState(false);
  const [assist, setAssist] = useState<{ zone: Frac; exclude: string } | null>(null);
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [launcherFilter, setLauncherFilter] = useState("");
  const [layoutFor, setLayoutFor] = useState<string | null>(null);
  // Empty panes of a preset split layout, waiting for an app to be picked.
  const [pending, setPending] = useState<Frac[]>([]);
  const [pendFilters, setPendFilters] = useState<Record<number, string>>({});
  // Per-window ✎ edit bar (keyed by mod — windows are one-per-mod).
  const [editOpen, setEditOpen] = useState<Record<string, boolean>>({});
  const [editText, setEditText] = useState<Record<string, string>>({});
  const [editBusy, setEditBusy] = useState<Record<string, boolean>>({});
  const [editNote, setEditNote] = useState<Record<string, { msg: string; err: boolean }>>({});
  const deskRef = useRef<HTMLDivElement>(null);
  const drag = useRef<DragState | null>(null);
  const nextZ = useRef(10);
  const loaded = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Snapped windows are sized as fractions of the desk — re-render on
  // viewport resize so they track the screen.
  const [, bumpSize] = useState(0);
  useEffect(() => {
    const onR = () => bumpSize((n) => n + 1);
    window.addEventListener("resize", onR);
    return () => window.removeEventListener("resize", onR);
  }, []);

  // Usable desk area. The desk element already stops above the taskbar, so
  // only the no-element fallback subtracts it (measuring it twice left a
  // dead 40px strip under every snapped window).
  const deskSize = () => {
    const el = deskRef.current;
    return {
      W: el?.clientWidth || window.innerWidth,
      H: el?.clientHeight || window.innerHeight - TASKBAR_H,
    };
  };

  // ── Persistence: layout only (mod + geometry), tiny and quota-safe. ──
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        const data = JSON.parse(raw);
        if (Array.isArray(data?.wins)) {
          const prof = (w: any): string | undefined =>
            typeof w.profile === "string" && /^[A-Za-z0-9_-]{1,32}$/.test(w.profile)
              ? w.profile
              : undefined;
          const restored: Win[] = data.wins
            .filter((w: any) => w && typeof w.mod === "string" && w.mod)
            // One window per mod+profile, and ids must match openWin's winId
            // scheme so a SPLIT for a restored mod raises it, not a dupe.
            .filter(
              (w: any, i: number, a: any[]) =>
                a.findIndex((x) => x.mod === w.mod && prof(x) === prof(w)) === i
            )
            .slice(0, 12)
            .map((w: any, i: number) => ({
              id: winId(w.mod, prof(w)),
              mod: w.mod,
              profile: prof(w),
              rect: {
                x: Math.max(0, Number(w.rect?.x) || 40 + i * 28),
                y: Math.max(0, Number(w.rect?.y) || 30 + i * 24),
                w: Math.max(MIN_W, Number(w.rect?.w) || 860),
                h: Math.max(MIN_H, Number(w.rect?.h) || 560),
              },
              snap:
                w.snap && typeof w.snap.x === "number"
                  ? { x: w.snap.x, y: w.snap.y, w: w.snap.w, h: w.snap.h }
                  : null,
              restore: null,
              z: 10 + i,
              min: !!w.min,
              gen: 0,
            }));
          nextZ.current = 10 + restored.length + 1;
          setWins(restored);
        }
      }
    } catch {}
    loaded.current = true;
  }, []);

  useEffect(() => {
    if (!loaded.current) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      try {
        const slim = wins.map((w) => ({ mod: w.mod, profile: w.profile, rect: w.rect, snap: w.snap, min: w.min }));
        const s = JSON.stringify({ v: 1, wins: slim });
        if (s.length < 8192) localStorage.setItem(STORE_KEY, s);
      } catch {}
    }, 400);
  }, [wins]);

  const bringToFront = useCallback((id: string) => {
    setFocusId(id);
    setWins((ws) => ws.map((w) => (w.id === id ? { ...w, z: ++nextZ.current } : w)));
  }, []);

  // Windows are one-per-mod-per-profile (openWin dedupes), so the id can be
  // derived from those. That keeps openWin idempotent — StrictMode
  // double-fires the SPLIT launch effect in dev, and the second call must
  // land on the same window — and lets focus be set without reading state
  // back out of a setWins updater (updaters run during render; ids computed
  // inside one can't be read reliably after the call).
  const openWin = useCallback(
    (mod: string, profile?: string) => {
      const { W, H } = deskSize();
      const id = winId(mod, profile);
      const w0 = Math.min(900, Math.max(MIN_W, Math.round(W * 0.6)));
      const h0 = Math.min(620, Math.max(MIN_H, Math.round(H * 0.72)));
      setWins((ws) => {
        // Re-focus the existing window for this mod instead of stacking dupes.
        if (ws.some((w) => w.id === id)) {
          return ws.map((w) => (w.id === id ? { ...w, min: false, z: ++nextZ.current } : w));
        }
        const i = ws.length;
        return [
          ...ws,
          {
            id,
            mod,
            profile,
            rect: {
              x: Math.max(8, Math.min(W - w0 - 8, 48 + (i % 6) * 32)),
              y: Math.max(8, Math.min(H - h0 - 8, 32 + (i % 6) * 26)),
              w: w0,
              h: h0,
            },
            snap: null,
            restore: null,
            z: ++nextZ.current,
            min: false,
            gen: 0,
          },
        ];
      });
      setFocusId(id);
    },
    []
  );

  // The SPLIT button in the console seeds/raises a window for the active
  // mod. A layout launch (cells) also snaps it into the first pane and turns
  // the remaining panes into "pick an app" slots. Idempotent, so the dev
  // StrictMode double-fire is harmless.
  useEffect(() => {
    if (!launch?.mod) return;
    openWin(launch.mod);
    if (launch.cells && launch.cells.length > 0) {
      snapTo(winId(launch.mod), launch.cells[0]);
      let rest = launch.cells.slice(1);
      // Dual-account split: the second pane is the SAME app on its own
      // storage profile — a fully separate session to sign into.
      if (launch.dual && rest.length > 0) {
        openWin(launch.mod, "2");
        snapTo(winId(launch.mod, "2"), rest[0]);
        rest = rest.slice(1);
      }
      setPending(rest);
      setPendFilters({});
      setAssist(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [launch?.n]);

  // ⧉ titlebar button: open another instance of a mod's app on the lowest
  // free profile — its storage (session, wallet, theme) is fully isolated,
  // so it can be signed into a different account.
  const openInstance = (mod: string) => {
    const used = new Set(wins.filter((w) => w.mod === mod).map((w) => w.profile || "1"));
    let n = 2;
    while (used.has(String(n))) n++;
    openWin(mod, String(n));
  };

  const closeWin = (id: string) =>
    setWins((ws) => ws.filter((w) => w.id !== id));

  // When a module's running-task count drops to zero, reload its window so
  // the pane shows the freshly edited app (jobs poll every ~4s upstream).
  const prevRunning = useRef<Record<string, number>>({});
  useEffect(() => {
    if (!tasks) return;
    const finished = new Set<string>();
    for (const [mod, was] of Object.entries(prevRunning.current)) {
      if (was > 0 && (tasks[mod]?.running || 0) === 0) finished.add(mod);
    }
    const snap: Record<string, number> = {};
    for (const [mod, t] of Object.entries(tasks)) snap[mod] = t.running || 0;
    prevRunning.current = snap;
    if (finished.size === 0) return;
    setWins((ws) => {
      if (!ws.some((w) => finished.has(w.mod))) return ws;
      return ws.map((w) => (finished.has(w.mod) ? { ...w, gen: w.gen + 1 } : w));
    });
    setEditNote((n) => {
      const next = { ...n };
      for (const mod of finished) next[mod] = { msg: "task finished — app reloaded", err: false };
      return next;
    });
  }, [tasks]);

  const sendEdit = async (mod: string) => {
    const text = (editText[mod] || "").trim();
    if (!text || !onEdit || editBusy[mod]) return;
    setEditBusy((b) => ({ ...b, [mod]: true }));
    setEditNote((n) => ({ ...n, [mod]: { msg: "sending…", err: false } }));
    try {
      const err = await onEdit(mod, text);
      if (err) setEditNote((n) => ({ ...n, [mod]: { msg: err, err: true } }));
      else {
        setEditText((t) => ({ ...t, [mod]: "" }));
        setEditNote((n) => ({ ...n, [mod]: { msg: "task started — pane reloads when it finishes", err: false } }));
      }
    } catch (e: any) {
      setEditNote((n) => ({ ...n, [mod]: { msg: e?.message || "submit failed", err: true } }));
    } finally {
      setEditBusy((b) => ({ ...b, [mod]: false }));
    }
  };

  const snapTo = useCallback((id: string, frac: Frac, withAssist = false) => {
    setWins((ws) =>
      ws.map((w) =>
        w.id === id
          ? {
              ...w,
              snap: frac,
              min: false,
              restore: w.snap ? w.restore : { w: w.rect.w, h: w.rect.h },
              z: ++nextZ.current,
            }
          : w
      )
    );
    setFocusId(id);
    // Snap-assist: a half just filled → offer the other windows for the
    // complementary half (the Windows split-screen flow).
    if (withAssist && frac.h === 1 && Math.abs(frac.w - 0.5) < 0.001) {
      const other: Frac = frac.x < 0.25 ? ZONES.right : ZONES.left;
      setWins((ws) => {
        const candidates = ws.filter(
          (w) => w.id !== id && !w.min && !sameFrac(w.snap, other)
        );
        setAssist(candidates.length > 0 ? { zone: other, exclude: id } : null);
        return ws;
      });
    }
  }, []);

  const toggleMax = (id: string) =>
    setWins((ws) =>
      ws.map((w) => {
        if (w.id !== id) return w;
        if (sameFrac(w.snap, ZONES.max)) {
          const back = w.restore || { w: 860, h: 560 };
          return { ...w, snap: null, rect: { ...w.rect, w: back.w, h: back.h }, z: ++nextZ.current };
        }
        return {
          ...w,
          snap: ZONES.max,
          restore: w.snap ? w.restore : { w: w.rect.w, h: w.rect.h },
          z: ++nextZ.current,
        };
      })
    );

  // Which snap zone does a pointer position arm? Corners win over edges;
  // the top edge maximizes, exactly like Windows.
  const zoneAt = (px: number, py: number, W: number, H: number): Frac | null => {
    if (px < EDGE) return py < H * 0.25 ? ZONES.tl : py > H * 0.75 ? ZONES.bl : ZONES.left;
    if (px > W - EDGE) return py < H * 0.25 ? ZONES.tr : py > H * 0.75 ? ZONES.br : ZONES.right;
    if (py < EDGE) return px < W * 0.2 ? ZONES.tl : px > W * 0.8 ? ZONES.tr : ZONES.max;
    if (py > H - EDGE) return px < W * 0.2 ? ZONES.bl : px > W * 0.8 ? ZONES.br : ZONES.bottom;
    return null;
  };

  // Current on-screen px rect for a window (snapped rects get a small gap
  // inset so tiled windows read as separate panes).
  const pxRect = (w: Win, W: number, H: number): Rect => {
    if (w.snap) {
      const g = sameFrac(w.snap, ZONES.max) ? 0 : GAP;
      return {
        x: Math.round(w.snap.x * W) + g,
        y: Math.round(w.snap.y * H) + g,
        w: Math.round(w.snap.w * W) - g * 2,
        h: Math.round(w.snap.h * H) - g * 2,
      };
    }
    return w.rect;
  };

  // ── Pointer-driven move / resize (pointer capture keeps events flowing
  //    even over iframes; iframes also go inert while interacting). ──
  const beginDrag = (
    e: React.PointerEvent,
    id: string,
    mode: "move" | "resize",
    edges = ""
  ) => {
    if (e.button !== 0) return;
    const w = wins.find((x) => x.id === id);
    if (!w) return;
    const { W, H } = deskSize();
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { id, mode, edges, startX: e.clientX, startY: e.clientY, orig: pxRect(w, W, H), moved: false };
    bringToFront(id);
    setInteracting(true);
    setLayoutFor(null);
  };

  const onDragMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const desk = deskRef.current;
    if (!desk) return;
    const rect = desk.getBoundingClientRect();
    const { W, H } = deskSize();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
    d.moved = true;

    if (d.mode === "move") {
      setWins((ws) =>
        ws.map((w) => {
          if (w.id !== d.id) return w;
          if (w.snap) {
            // Dragging a snapped/maximized window peels it off: restore the
            // floating size and keep the grab point under the cursor.
            const back = w.restore || { w: Math.round(W * 0.6), h: Math.round(H * 0.6) };
            const relX = Math.min(0.9, Math.max(0.1, (d.startX - rect.left - d.orig.x) / d.orig.w));
            const nr = {
              x: Math.round(px - back.w * relX),
              y: Math.max(0, py - TITLE_H / 2),
              w: back.w,
              h: back.h,
            };
            d.orig = nr;
            d.startX = e.clientX;
            d.startY = e.clientY;
            return { ...w, snap: null, rect: nr };
          }
          return {
            ...w,
            rect: {
              ...w.rect,
              x: Math.min(W - 80, Math.max(80 - w.rect.w, d.orig.x + dx)),
              y: Math.min(H - TITLE_H, Math.max(0, d.orig.y + dy)),
            },
          };
        })
      );
      setPreview(zoneAt(px, py, W, H));
    } else {
      setWins((ws) =>
        ws.map((w) => {
          if (w.id !== d.id) return w;
          let { x, y, w: ww, h } = d.orig;
          if (d.edges.includes("e")) ww = Math.max(MIN_W, d.orig.w + dx);
          if (d.edges.includes("s")) h = Math.max(MIN_H, d.orig.h + dy);
          if (d.edges.includes("w")) {
            ww = Math.max(MIN_W, d.orig.w - dx);
            x = d.orig.x + (d.orig.w - ww);
          }
          if (d.edges.includes("n")) {
            h = Math.max(MIN_H, d.orig.h - dy);
            y = d.orig.y + (d.orig.h - h);
          }
          // Resizing a snapped window makes it floating at its visual rect.
          return { ...w, snap: null, rect: { x, y, w: ww, h } };
        })
      );
    }
  };

  const endDrag = () => {
    const d = drag.current;
    drag.current = null;
    setInteracting(false);
    if (d && d.mode === "move" && d.moved && preview) {
      snapTo(d.id, preview, true);
    }
    setPreview(null);
  };

  // Esc: dismiss popups first, then leave the Desk.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (launcherOpen || assist || layoutFor || pending.length > 0) {
        setLauncherOpen(false);
        setAssist(null);
        setLayoutFor(null);
        setPending([]);
      } else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, launcherOpen, assist, layoutFor, pending.length, onClose]);

  const base = origin.replace(/\/+$/, "");
  const visible = wins.filter((w) => !w.min);
  const { W: dW, H: dH } =
    typeof window !== "undefined" && deskRef.current ? deskSize() : { W: 1600, H: 900 };

  const launcherMods = mods
    .filter((m, i) => mods.indexOf(m) === i)
    .filter((m) => !launcherFilter || m.toLowerCase().includes(launcherFilter.toLowerCase()))
    .sort();

  const btnStyle = (on: boolean, c: string): React.CSSProperties => ({
    color: on ? c : "var(--text-tertiary)",
    background: on ? `color-mix(in srgb, ${c} 12%, transparent)` : "transparent",
    border: `1px solid ${on ? `color-mix(in srgb, ${c} 40%, transparent)` : "var(--border-color)"}`,
  });

  return (
    <div
      style={{
        position: "fixed",
        // Dock BELOW the console header (--header-h, published by the page's
        // header ResizeObserver): a split divides the MOD VIEWS, it doesn't
        // swallow the console. The address bar, tabs and SPLIT picker stay
        // reachable at the top while the panes run underneath.
        top: "var(--header-h, 0px)",
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 300,
        display: open ? "block" : "none",
        background:
          "radial-gradient(ellipse at 20% 10%, color-mix(in srgb, var(--crt-green) 5%, transparent), transparent 55%), radial-gradient(ellipse at 85% 90%, color-mix(in srgb, var(--crt-blue) 5%, transparent), transparent 55%), var(--bg-primary)",
        userSelect: interacting ? "none" : undefined,
      }}
    >
      {/* Desk area (windows live here; taskbar sits below) */}
      <div ref={deskRef} style={{ position: "absolute", inset: `0 0 ${TASKBAR_H}px 0`, overflow: "hidden" }}>
        {/* Snap-zone preview while dragging */}
        {preview && (
          <div
            style={{
              position: "absolute",
              left: preview.x * dW + GAP,
              top: preview.y * dH + GAP,
              width: preview.w * dW - GAP * 2,
              height: preview.h * dH - GAP * 2,
              zIndex: 5000,
              pointerEvents: "none",
              borderRadius: 12,
              background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
              border: "1px solid color-mix(in srgb, var(--crt-green) 45%, transparent)",
              boxShadow: "0 0 40px color-mix(in srgb, var(--crt-green) 18%, transparent) inset",
              transition: "left .12s ease, top .12s ease, width .12s ease, height .12s ease",
            }}
          />
        )}

        {wins.length === 0 && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
              color: "var(--text-tertiary)",
            }}
          >
            <span style={{ fontSize: 42, opacity: 0.25 }}>⊞</span>
            <span style={{ fontSize: 12, letterSpacing: "0.14em", textTransform: "uppercase" }}>
              No windows — add an app from the taskbar
            </span>
          </div>
        )}

        {wins.map((w) => {
          const r = pxRect(w, dW, dH);
          const focused = focusId === w.id;
          const maxed = sameFrac(w.snap, ZONES.max);
          const dragging = drag.current?.id === w.id;
          return (
            <div
              key={w.id}
              // Raise unconditionally: a window can be "focused" yet sit
              // below a later-opened one, and its ⊞ snap-layout popup would
              // then render underneath that window, unclickable.
              onPointerDownCapture={() => bringToFront(w.id)}
              style={{
                position: "absolute",
                left: r.x,
                top: r.y,
                width: r.w,
                height: r.h,
                zIndex: w.z,
                display: w.min ? "none" : "flex",
                flexDirection: "column",
                borderRadius: maxed ? 0 : 10,
                overflow: "hidden",
                background: "var(--bg-primary)",
                border: `1px solid ${
                  focused
                    ? "color-mix(in srgb, var(--crt-green) 45%, var(--border-color))"
                    : "var(--border-color)"
                }`,
                boxShadow: focused
                  ? "0 18px 60px rgba(0,0,0,0.55), 0 0 0 1px color-mix(in srgb, var(--crt-green) 14%, transparent)"
                  : "0 10px 34px rgba(0,0,0,0.42)",
                transition: dragging
                  ? "none"
                  : "left .18s cubic-bezier(.2,.8,.2,1), top .18s cubic-bezier(.2,.8,.2,1), width .18s cubic-bezier(.2,.8,.2,1), height .18s cubic-bezier(.2,.8,.2,1)",
              }}
            >
              {/* Titlebar — drag to move/snap, double-click to maximize */}
              <div
                onPointerDown={(e) => beginDrag(e, w.id, "move")}
                onPointerMove={onDragMove}
                onPointerUp={endDrag}
                onDoubleClick={() => toggleMax(w.id)}
                style={{
                  height: TITLE_H,
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "0 8px",
                  cursor: dragging ? "grabbing" : "grab",
                  touchAction: "none",
                  background: focused
                    ? "linear-gradient(180deg, color-mix(in srgb, var(--crt-green) 8%, var(--bg-secondary)), var(--bg-secondary))"
                    : "var(--bg-secondary)",
                  borderBottom: "1px solid var(--border-color)",
                }}
              >
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: focused ? "var(--crt-green)" : "var(--text-tertiary)",
                    boxShadow: focused ? "0 0 8px var(--crt-green)" : "none",
                  }}
                />
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                    color: focused ? "var(--text-primary)" : "var(--text-tertiary)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {w.mod}
                </span>
                <span style={{ fontSize: 9, color: "var(--text-tertiary)", opacity: 0.6, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  /{w.mod}{w.profile ? `?profile=${w.profile}` : ""}
                </span>
                {w.profile && (
                  <span
                    title={`Separate instance (profile ${w.profile}) — isolated storage and its own sign-in`}
                    style={{
                      flexShrink: 0,
                      fontSize: 8.5,
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      padding: "1px 6px",
                      borderRadius: 5,
                      color: "var(--crt-blue)",
                      background: "color-mix(in srgb, var(--crt-blue) 12%, transparent)",
                      border: "1px solid color-mix(in srgb, var(--crt-blue) 35%, transparent)",
                    }}
                  >
                    ⧉ {w.profile}
                  </span>
                )}
                {(tasks?.[w.mod]?.running || 0) > 0 ? (
                  <span
                    title={`${tasks![w.mod].running} task${tasks![w.mod].running === 1 ? "" : "s"} running on ${w.mod}`}
                    style={{
                      flexShrink: 0,
                      fontSize: 8.5,
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      padding: "1px 6px",
                      borderRadius: 5,
                      color: "var(--crt-amber)",
                      background: "color-mix(in srgb, var(--crt-amber) 12%, transparent)",
                      border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                      animation: "soft-pulse 1.6s ease-in-out infinite",
                    }}
                  >
                    ▶ {tasks![w.mod].running}
                  </span>
                ) : (tasks?.[w.mod]?.pending || 0) > 0 ? (
                  <span
                    title={`${tasks![w.mod].pending} task${tasks![w.mod].pending === 1 ? "" : "s"} queued for ${w.mod}`}
                    style={{
                      flexShrink: 0,
                      fontSize: 8.5,
                      fontWeight: 700,
                      padding: "1px 6px",
                      borderRadius: 5,
                      color: "var(--text-tertiary)",
                      border: "1px solid var(--border-color)",
                    }}
                  >
                    … {tasks![w.mod].pending}
                  </span>
                ) : null}
                <span style={{ flex: 1 }} />
                {/* Window controls (pointerDown stopped so buttons never start a drag) */}
                <span
                  onPointerDown={(e) => e.stopPropagation()}
                  style={{ display: "flex", alignItems: "center", gap: 2, flexShrink: 0 }}
                >
                  {onEdit && (
                    <TbBtn
                      title={`Edit ${w.mod} — send the agent a task from this pane`}
                      active={!!editOpen[w.mod]}
                      onClick={() => setEditOpen((o) => ({ ...o, [w.mod]: !o[w.mod] }))}
                    >
                      ✎
                    </TbBtn>
                  )}
                  <TbBtn
                    title={`New instance of ${w.mod} — isolated session, sign into a different account (apps honoring ?profile=)`}
                    onClick={() => openInstance(w.mod)}
                  >
                    ⧉
                  </TbBtn>
                  <TbBtn title="Reload" onClick={() => setWins((ws) => ws.map((x) => (x.id === w.id ? { ...x, gen: x.gen + 1 } : x)))}>↻</TbBtn>
                  <span style={{ position: "relative" }}>
                    <TbBtn
                      title="Snap layouts — pick a screen region"
                      active={layoutFor === w.id}
                      onClick={() => setLayoutFor((v) => (v === w.id ? null : w.id))}
                    >
                      ⊞
                    </TbBtn>
                    {layoutFor === w.id && (
                      <div
                        style={{
                          position: "absolute",
                          top: 24,
                          right: -8,
                          zIndex: 6000,
                          display: "grid",
                          gridTemplateColumns: "repeat(3, auto)",
                          gap: 8,
                          padding: 10,
                          borderRadius: 12,
                          background: "color-mix(in srgb, var(--bg-secondary) 92%, transparent)",
                          border: "1px solid var(--border-color)",
                          boxShadow: "0 14px 44px rgba(0,0,0,0.55)",
                          backdropFilter: "blur(14px) saturate(140%)",
                          WebkitBackdropFilter: "blur(14px) saturate(140%)",
                        }}
                      >
                        {LAYOUTS.map((cells, li) => (
                          <div
                            key={li}
                            style={{
                              position: "relative",
                              width: 66,
                              height: 42,
                              borderRadius: 6,
                              background: "var(--bg-primary)",
                              border: "1px solid var(--border-color)",
                              overflow: "hidden",
                            }}
                          >
                            {cells.map((c, ci) => (
                              <div
                                key={ci}
                                onClick={() => { snapTo(w.id, c); setLayoutFor(null); }}
                                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "color-mix(in srgb, var(--crt-green) 35%, transparent)"; }}
                                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "color-mix(in srgb, var(--crt-green) 10%, transparent)"; }}
                                title="Snap here"
                                style={{
                                  position: "absolute",
                                  left: `${c.x * 100}%`,
                                  top: `${c.y * 100}%`,
                                  width: `${c.w * 100}%`,
                                  height: `${c.h * 100}%`,
                                  boxSizing: "border-box",
                                  border: "1px solid color-mix(in srgb, var(--crt-green) 30%, var(--border-color))",
                                  background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                                  cursor: "pointer",
                                  transition: "background .1s ease",
                                }}
                              />
                            ))}
                          </div>
                        ))}
                      </div>
                    )}
                  </span>
                  <TbBtn title="Minimize to taskbar" onClick={() => setWins((ws) => ws.map((x) => (x.id === w.id ? { ...x, min: true } : x)))}>—</TbBtn>
                  <TbBtn title={maxed ? "Restore" : "Maximize"} onClick={() => toggleMax(w.id)}>{maxed ? "❐" : "□"}</TbBtn>
                  <TbBtn title="Close window" danger onClick={() => closeWin(w.id)}>✕</TbBtn>
                </span>
              </div>

              {/* App iframe — inert while any drag/resize is in flight so it
                  can't swallow pointer events */}
              <iframe
                key={w.gen}
                src={winSrc(base, w.mod, w.profile)}
                title={w.profile ? `${w.mod} (profile ${w.profile})` : w.mod}
                style={{ flex: 1, border: 0, width: "100%", pointerEvents: interacting ? "none" : "auto", background: "var(--bg-primary)" }}
              />

              {/* ✎ edit bar — drive the agent on this app without leaving the
                  split screen. One bar per window, so several apps can have
                  tasks in flight at once. */}
              {onEdit && editOpen[w.mod] && (
                <div
                  onPointerDown={(e) => e.stopPropagation()}
                  style={{
                    flexShrink: 0,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                    padding: "6px 8px",
                    background: "var(--bg-secondary)",
                    borderTop: "1px solid var(--border-color)",
                  }}
                >
                  <div style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
                    <textarea
                      rows={2}
                      value={editText[w.mod] || ""}
                      onChange={(e) => setEditText((t) => ({ ...t, [w.mod]: e.target.value }))}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendEdit(w.mod);
                        }
                      }}
                      placeholder={`edit ${w.mod}… (Enter to send, Shift+Enter for a new line)`}
                      style={{
                        flex: 1,
                        resize: "none",
                        padding: "6px 9px",
                        fontSize: 11,
                        lineHeight: 1.45,
                        fontFamily: "inherit",
                        outline: "none",
                        borderRadius: 8,
                        color: "var(--text-primary)",
                        background: "var(--bg-primary)",
                        border: "1px solid var(--border-color)",
                      }}
                    />
                    <button
                      onClick={() => sendEdit(w.mod)}
                      disabled={editBusy[w.mod] || !(editText[w.mod] || "").trim()}
                      style={{
                        padding: "7px 13px",
                        borderRadius: 8,
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: "0.1em",
                        cursor: editBusy[w.mod] || !(editText[w.mod] || "").trim() ? "default" : "pointer",
                        opacity: editBusy[w.mod] || !(editText[w.mod] || "").trim() ? 0.5 : 1,
                        color: "var(--crt-green)",
                        background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                        border: "1px solid color-mix(in srgb, var(--crt-green) 35%, transparent)",
                      }}
                    >
                      {editBusy[w.mod] ? "…" : "SEND"}
                    </button>
                  </div>
                  {editNote[w.mod] && (
                    <span
                      style={{
                        fontSize: 9,
                        letterSpacing: "0.04em",
                        color: editNote[w.mod].err ? "var(--crt-red, #ff5f56)" : "var(--text-tertiary)",
                      }}
                    >
                      {editNote[w.mod].msg}
                    </span>
                  )}
                </div>
              )}

              {/* Resize handles (grab an edge or corner) */}
              {!maxed &&
                ([
                  ["n", { top: -3, left: 8, right: 8, height: 7, cursor: "ns-resize" }],
                  ["s", { bottom: -3, left: 8, right: 8, height: 7, cursor: "ns-resize" }],
                  ["e", { right: -3, top: 8, bottom: 8, width: 7, cursor: "ew-resize" }],
                  ["w", { left: -3, top: 8, bottom: 8, width: 7, cursor: "ew-resize" }],
                  ["nw", { top: -3, left: -3, width: 12, height: 12, cursor: "nwse-resize" }],
                  ["ne", { top: -3, right: -3, width: 12, height: 12, cursor: "nesw-resize" }],
                  ["sw", { bottom: -3, left: -3, width: 12, height: 12, cursor: "nesw-resize" }],
                  ["se", { bottom: -3, right: -3, width: 12, height: 12, cursor: "nwse-resize" }],
                ] as Array<[string, React.CSSProperties]>).map(([edges, css]) => (
                  <div
                    key={edges}
                    onPointerDown={(e) => beginDrag(e, w.id, "resize", edges)}
                    onPointerMove={onDragMove}
                    onPointerUp={endDrag}
                    style={{ position: "absolute", zIndex: 20, touchAction: "none", ...css }}
                  />
                ))}
            </div>
          );
        })}

        {/* Snap-assist: a half just filled — offer the other windows for the
            empty half, Windows style */}
        {assist && (
          <div
            onClick={() => setAssist(null)}
            style={{
              position: "absolute",
              left: assist.zone.x * dW + GAP,
              top: assist.zone.y * dH + GAP,
              width: assist.zone.w * dW - GAP * 2,
              height: assist.zone.h * dH - GAP * 2,
              zIndex: 5500,
              borderRadius: 12,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 14,
              background: "color-mix(in srgb, var(--bg-secondary) 72%, transparent)",
              border: "1px dashed color-mix(in srgb, var(--crt-green) 35%, transparent)",
              backdropFilter: "blur(10px) saturate(130%)",
              WebkitBackdropFilter: "blur(10px) saturate(130%)",
            }}
          >
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
              Snap a window here
            </span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center", maxWidth: "84%" }}>
              {wins
                .filter((w) => w.id !== assist.exclude && !w.min && !sameFrac(w.snap, assist.zone))
                .map((w) => (
                  <button
                    key={w.id}
                    onClick={(e) => { e.stopPropagation(); snapTo(w.id, assist.zone); setAssist(null); }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "10px 16px",
                      borderRadius: 10,
                      cursor: "pointer",
                      fontSize: 11,
                      fontWeight: 700,
                      letterSpacing: "0.1em",
                      textTransform: "uppercase",
                      color: "var(--text-primary)",
                      background: "color-mix(in srgb, var(--crt-green) 8%, var(--bg-primary))",
                      border: "1px solid color-mix(in srgb, var(--crt-green) 30%, var(--border-color))",
                    }}
                  >
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--crt-green)" }} />
                    {w.mod}{w.profile ? ` ⧉${w.profile}` : ""}
                  </button>
                ))}
            </div>
            <span style={{ fontSize: 9, color: "var(--text-tertiary)", opacity: 0.7 }}>click anywhere to dismiss</span>
          </div>
        )}

        {/* Preset-layout empty panes: each offers a filterable app picker so
            a 2/3/4-way split screen fills up in a few clicks. */}
        {pending.map((cell, i) => {
          const filter = (pendFilters[i] || "").toLowerCase();
          const cellMods = mods
            .filter((m, mi) => mods.indexOf(m) === mi)
            .filter((m) => !filter || m.toLowerCase().includes(filter))
            .sort();
          const pick = (m: string) => {
            openWin(m);
            snapTo(winId(m), cell);
            setPending((p) => p.filter((_, j) => j !== i));
            setPendFilters({});
          };
          return (
            <div
              key={`pend-${i}`}
              style={{
                position: "absolute",
                left: cell.x * dW + GAP,
                top: cell.y * dH + GAP,
                width: cell.w * dW - GAP * 2,
                height: cell.h * dH - GAP * 2,
                zIndex: 5400,
                borderRadius: 12,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 10,
                padding: 16,
                background: "color-mix(in srgb, var(--bg-secondary) 72%, transparent)",
                border: "1px dashed color-mix(in srgb, var(--crt-blue) 40%, transparent)",
                backdropFilter: "blur(10px) saturate(130%)",
                WebkitBackdropFilter: "blur(10px) saturate(130%)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
                  Pick an app for this pane
                </span>
                <button
                  onClick={() => { setPending((p) => p.filter((_, j) => j !== i)); setPendFilters({}); }}
                  title="Leave this pane empty"
                  style={{
                    width: 20,
                    height: 20,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: 5,
                    fontSize: 10,
                    cursor: "pointer",
                    color: "var(--text-tertiary)",
                    background: "transparent",
                    border: "1px solid var(--border-color)",
                  }}
                >
                  ✕
                </button>
              </div>
              <input
                value={pendFilters[i] || ""}
                onChange={(e) => setPendFilters((f) => ({ ...f, [i]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && cellMods.length > 0) pick(cellMods[0]);
                }}
                placeholder="filter apps…"
                style={{
                  width: "min(240px, 90%)",
                  padding: "7px 11px",
                  fontSize: 11,
                  fontFamily: "inherit",
                  outline: "none",
                  borderRadius: 8,
                  color: "var(--text-primary)",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border-color)",
                }}
              />
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 8,
                  justifyContent: "center",
                  maxWidth: "92%",
                  maxHeight: "55%",
                  overflowY: "auto",
                  padding: 2,
                }}
              >
                {cellMods.length === 0 && (
                  <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>no module apps found</span>
                )}
                {cellMods.map((m) => (
                  <button
                    key={m}
                    onClick={() => pick(m)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 7,
                      padding: "7px 13px",
                      borderRadius: 9,
                      cursor: "pointer",
                      fontSize: 10.5,
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      textTransform: "uppercase",
                      color: "var(--text-primary)",
                      background: "color-mix(in srgb, var(--crt-blue) 8%, var(--bg-primary))",
                      border: "1px solid color-mix(in srgb, var(--crt-blue) 30%, var(--border-color))",
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "color-mix(in srgb, var(--crt-blue) 20%, var(--bg-primary))"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "color-mix(in srgb, var(--crt-blue) 8%, var(--bg-primary))"; }}
                  >
                    <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--crt-blue)", flexShrink: 0 }} />
                    {m}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {/* Taskbar */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: TASKBAR_H,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 10px",
          background: "color-mix(in srgb, var(--bg-secondary) 88%, transparent)",
          borderTop: "1px solid var(--border-color)",
          backdropFilter: "blur(14px) saturate(140%)",
          WebkitBackdropFilter: "blur(14px) saturate(140%)",
        }}
      >
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.14em", color: "var(--crt-green)", flexShrink: 0 }}>
          ⊞ DESK
        </span>
        <span style={{ fontSize: 9, color: "var(--text-tertiary)", flexShrink: 0 }}>
          {visible.length}/{wins.length} open · drag a title to an edge to snap
        </span>
        <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 6, overflowX: "auto", padding: "0 4px" }}>
          {wins.map((w) => {
            const active = focusId === w.id && !w.min;
            return (
              <button
                key={w.id}
                onClick={() =>
                  w.min || !active
                    ? (setWins((ws) => ws.map((x) => (x.id === w.id ? { ...x, min: false, z: ++nextZ.current } : x))), setFocusId(w.id))
                    : setWins((ws) => ws.map((x) => (x.id === w.id ? { ...x, min: true } : x)))
                }
                title={w.min ? `Restore ${w.mod}` : active ? `Minimize ${w.mod}` : `Focus ${w.mod}`}
                className="shrink-0"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: 7,
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                  opacity: w.min ? 0.5 : 1,
                  ...btnStyle(active, "var(--crt-green)"),
                }}
              >
                <span style={{ width: 5, height: 5, borderRadius: "50%", background: w.min ? "var(--text-tertiary)" : "var(--crt-green)" }} />
                {w.mod}{w.profile ? ` ⧉${w.profile}` : ""}
              </button>
            );
          })}
        </div>
        <span style={{ position: "relative", flexShrink: 0 }}>
          <button
            onClick={() => setLauncherOpen((v) => !v)}
            style={{ padding: "4px 10px", borderRadius: 7, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", cursor: "pointer", ...btnStyle(launcherOpen, "var(--crt-blue)") }}
            title="Open a module app in a new window"
          >
            + APP
          </button>
          {launcherOpen && (
            <div
              style={{
                position: "absolute",
                bottom: TASKBAR_H - 4,
                right: 0,
                zIndex: 6000,
                width: 300,
                maxHeight: 380,
                display: "flex",
                flexDirection: "column",
                borderRadius: 12,
                overflow: "hidden",
                background: "color-mix(in srgb, var(--bg-secondary) 94%, transparent)",
                border: "1px solid var(--border-color)",
                boxShadow: "0 -14px 44px rgba(0,0,0,0.5)",
                backdropFilter: "blur(14px) saturate(140%)",
                WebkitBackdropFilter: "blur(14px) saturate(140%)",
              }}
            >
              <input
                autoFocus
                value={launcherFilter}
                onChange={(e) => setLauncherFilter(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && launcherMods.length > 0) {
                    openWin(launcherMods[0]);
                    setLauncherOpen(false);
                    setLauncherFilter("");
                  }
                }}
                placeholder="filter apps…"
                style={{
                  padding: "9px 12px",
                  fontSize: 11,
                  fontFamily: "inherit",
                  outline: "none",
                  color: "var(--text-primary)",
                  background: "transparent",
                  border: "none",
                  borderBottom: "1px solid var(--border-color)",
                }}
              />
              <div style={{ overflowY: "auto", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, padding: 8 }}>
                {launcherMods.length === 0 && (
                  <span style={{ gridColumn: "1 / -1", fontSize: 10, color: "var(--text-tertiary)", padding: 8 }}>
                    no module apps found
                  </span>
                )}
                {launcherMods.map((m) => (
                  <button
                    key={m}
                    onClick={() => { openWin(m); setLauncherOpen(false); setLauncherFilter(""); }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 7,
                      padding: "7px 10px",
                      borderRadius: 8,
                      fontSize: 10.5,
                      fontWeight: 600,
                      cursor: "pointer",
                      textAlign: "left",
                      color: "var(--text-primary)",
                      background: "transparent",
                      border: "1px solid var(--border-color)",
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "color-mix(in srgb, var(--crt-blue) 10%, transparent)"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                  >
                    <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--crt-blue)", flexShrink: 0 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </span>
        <button
          onClick={onClose}
          style={{ padding: "4px 10px", borderRadius: 7, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", cursor: "pointer", flexShrink: 0, ...btnStyle(false, "var(--crt-amber)") }}
          title="Back to the console (windows keep running)"
        >
          ✕ EXIT
        </button>
      </div>
    </div>
  );
}

// Tiny titlebar icon button
function TbBtn({
  children,
  title,
  onClick,
  danger,
  active,
}: {
  children: React.ReactNode;
  title: string;
  onClick: () => void;
  danger?: boolean;
  active?: boolean;
}) {
  const c = danger ? "var(--crt-red, #ff5f56)" : "var(--crt-green)";
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 22,
        height: 20,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 5,
        fontSize: 11,
        lineHeight: 1,
        cursor: "pointer",
        color: active ? c : "var(--text-tertiary)",
        background: active ? `color-mix(in srgb, ${c} 14%, transparent)` : "transparent",
        border: "none",
        transition: "color .1s ease, background .1s ease",
      }}
      onMouseEnter={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.color = c;
        el.style.background = `color-mix(in srgb, ${c} 14%, transparent)`;
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.color = active ? c : "var(--text-tertiary)";
        el.style.background = active ? `color-mix(in srgb, ${c} 14%, transparent)` : "transparent";
      }}
    >
      {children}
    </button>
  );
}
