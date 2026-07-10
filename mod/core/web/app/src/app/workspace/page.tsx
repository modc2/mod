"use client";

// Workspace — a windowed multi-app canvas. Add up to four app windows one at a
// time with ＋, drag a window by its title bar and it snaps into a quarter of
// the canvas (windows auto-expand to fill unused halves, Windows-snap style —
// while you drag, the other windows live-preview their new spots). Any window
// expands to full canvas and back. A collapsible left rail lists previously
// visited apps for one-click opening. Layout persists (tiny) to localStorage.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, gatewayUrl, recents, type Module } from "@/lib/api";
import { Nav } from "../components/Chrome";

const MAX_WIN = 4;
const STORE_KEY = "mod.web.workspace.v2";
// Fill order for new windows: TL, TR, BR, BL reads naturally.
const FILL_ORDER = [0, 1, 3, 2];

function hue(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 62%)`;
}

// A window lives in one quadrant: 0 TL · 1 TR · 2 BL · 3 BR.
type Win = { id: number; quad: number; app: string | null };
type Rect = { t: number; l: number; w: number; h: number }; // fractions of canvas
type Drag = { id: number; dx: number; dy: number; quad: number };

// Quadrant occupancy → rendered rects. Windows expand into empty space:
// 1 window fills the canvas, 2 split in half along whichever axis separates
// them, 3 gives the lone column a full-height window, 4 is true quarters.
function layoutRects(wins: { id: number; quad: number }[]): Record<number, Rect> {
  const rects: Record<number, Rect> = {};
  const half = (r: number, c: number, rs: number, cs: number): Rect => ({
    t: r / 2,
    l: c / 2,
    h: rs / 2,
    w: cs / 2,
  });
  if (wins.length === 1) {
    rects[wins[0].id] = half(0, 0, 2, 2);
  } else if (wins.length === 2) {
    const [a, b] = wins;
    if ((a.quad & 1) !== (b.quad & 1)) {
      // different columns → side-by-side, each full height
      rects[a.id] = half(0, a.quad & 1, 2, 1);
      rects[b.id] = half(0, b.quad & 1, 2, 1);
    } else {
      // same column → stacked, each full width
      rects[a.id] = half(a.quad >> 1, 0, 1, 2);
      rects[b.id] = half(b.quad >> 1, 0, 1, 2);
    }
  } else if (wins.length === 3) {
    const occ = new Set(wins.map((w) => w.quad));
    const empty = [0, 1, 2, 3].find((q) => !occ.has(q))!;
    for (const w of wins) {
      const r = w.quad >> 1;
      const c = w.quad & 1;
      // the lone window in the empty slot's column gets the full column
      rects[w.id] = c === (empty & 1) ? half(0, c, 2, 1) : half(r, c, 1, 1);
    }
  } else {
    for (const w of wins) rects[w.id] = half(w.quad >> 1, w.quad & 1, 1, 1);
  }
  return rects;
}

const GAP = 10;
function rectStyle(r: Rect): React.CSSProperties {
  return {
    top: `calc(${r.t * 100}% + ${GAP / 2}px)`,
    left: `calc(${r.l * 100}% + ${GAP / 2}px)`,
    width: `calc(${r.w * 100}% - ${GAP}px)`,
    height: `calc(${r.h * 100}% - ${GAP}px)`,
  };
}

type Saved = { wins: { quad: number; app: string | null }[]; side: boolean };

function loadSaved(): Saved {
  if (typeof window === "undefined") return { wins: [], side: true };
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (raw) {
      const s = JSON.parse(raw) as Saved;
      if (Array.isArray(s.wins)) {
        return {
          wins: s.wins
            .filter((w) => Number.isInteger(w.quad) && w.quad >= 0 && w.quad < 4)
            .slice(0, MAX_WIN),
          side: s.side !== false,
        };
      }
    }
  } catch {
    /* corrupt / blocked storage */
  }
  return { wins: [], side: true };
}

export default function Workspace() {
  const init = useMemo(loadSaved, []);
  const idSeq = useRef(0);
  const [wins, setWins] = useState<Win[]>(() =>
    init.wins.map((w) => ({ id: ++idSeq.current, ...w })),
  );
  const [side, setSide] = useState(init.side);
  const [recent, setRecent] = useState<string[]>([]);
  const [mods, setMods] = useState<Module[] | null>(null);
  const [maximized, setMaximized] = useState<number | null>(null);
  const [picking, setPicking] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [fullPulse, setFullPulse] = useState(0);
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragOrigin = useRef<{ x: number; y: number } | null>(null);
  // per-window nonce so "reload" can force the iframe to remount
  const nonces = useRef<Record<number, number>>({});

  useEffect(() => {
    let alive = true;
    api
      .mods()
      .then((m) => alive && setMods(m.filter((x) => x.has_app)))
      .catch(() => alive && setMods([]));
    setRecent(recents.get());
    return () => {
      alive = false;
    };
  }, []);

  // persist (tiny payload — modc2's shared localStorage stays happy)
  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORE_KEY,
        JSON.stringify({ wins: wins.map(({ quad, app }) => ({ quad, app })), side }),
      );
    } catch {
      /* non-fatal */
    }
  }, [wins, side]);

  function addWindow(app: string | null = null) {
    if (wins.length >= MAX_WIN) {
      setFullPulse((n) => n + 1);
      return;
    }
    const occ = new Set(wins.map((w) => w.quad));
    const quad = FILL_ORDER.find((q) => !occ.has(q))!;
    const id = ++idSeq.current;
    setWins((prev) => [...prev, { id, quad, app }]);
    if (app) setRecent(recents.add(app));
    else setPicking(id);
  }

  function closeWindow(id: number) {
    setWins((prev) => prev.filter((w) => w.id !== id));
    setMaximized((m) => (m === id ? null : m));
    setPicking((p) => (p === id ? null : p));
  }

  function setApp(id: number, name: string) {
    setWins((prev) => prev.map((w) => (w.id === id ? { ...w, app: name } : w)));
    nonces.current[id] = (nonces.current[id] || 0) + 1;
    setRecent(recents.add(name));
    setPicking(null);
  }

  function reloadWindow(id: number) {
    nonces.current[id] = (nonces.current[id] || 0) + 1;
    setWins((prev) => prev.slice()); // re-render the keyed iframe
  }

  // ── drag & snap ──────────────────────────────────────────────────────────
  // Pointer capture on the title bar keeps events flowing even over iframes.

  function quadAt(x: number, y: number): number {
    const r = canvasRef.current?.getBoundingClientRect();
    if (!r) return 0;
    return (y > r.top + r.height / 2 ? 2 : 0) + (x > r.left + r.width / 2 ? 1 : 0);
  }

  function onGrab(e: React.PointerEvent<HTMLDivElement>, w: Win) {
    // buttons/links on the bar keep their click behavior
    if ((e.target as HTMLElement).closest("button, a")) return;
    if (maximized != null) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragOrigin.current = { x: e.clientX, y: e.clientY };
    setDrag({ id: w.id, dx: 0, dy: 0, quad: w.quad });
  }

  function onDragMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!drag || !dragOrigin.current) return;
    setDrag({
      ...drag,
      dx: e.clientX - dragOrigin.current.x,
      dy: e.clientY - dragOrigin.current.y,
      quad: quadAt(e.clientX, e.clientY),
    });
  }

  function onDrop() {
    if (drag) {
      const d = drag;
      setWins((prev) => {
        const me = prev.find((w) => w.id === d.id);
        if (!me || me.quad === d.quad) return prev;
        // land on an occupied quarter → the occupant takes our old spot
        return prev.map((w) =>
          w.id === d.id
            ? { ...w, quad: d.quad }
            : w.quad === d.quad
              ? { ...w, quad: me.quad }
              : w,
        );
      });
    }
    setDrag(null);
    dragOrigin.current = null;
  }

  // Committed layout, plus the hypothetical one shown live while dragging.
  const baseRects = useMemo(() => layoutRects(wins), [wins]);
  const previewRects = useMemo(() => {
    if (!drag) return baseRects;
    const me = wins.find((w) => w.id === drag.id);
    if (!me || me.quad === drag.quad) return baseRects;
    return layoutRects(
      wins.map((w) =>
        w.id === drag.id
          ? { ...w, quad: drag.quad }
          : w.quad === drag.quad
            ? { ...w, quad: me.quad }
            : w,
      ),
    );
  }, [drag, wins, baseRects]);

  const dragMoved = drag ? Math.hypot(drag.dx, drag.dy) > 6 : false;
  const openApps = useMemo(() => new Set(wins.map((w) => w.app)), [wins]);
  const recentMods = useMemo(
    () =>
      recent
        .map((n) => mods?.find((m) => m.name === n))
        .filter((m): m is Module => !!m),
    [recent, mods],
  );

  return (
    <div className="ws-root">
      <Nav />

      <div className="ws-bar">
        <Link href="/" className="ws-back">
          ← explorer
        </Link>
        <div className="ws-title" key={fullPulse} data-pulse={fullPulse > 0 || undefined}>
          <span className="ws-title-glyph" aria-hidden>
            ▦
          </span>
          Workspace
        </div>
        <div
          className="ws-pips"
          title={`${wins.length} of ${MAX_WIN} windows`}
          aria-label={`${wins.length} of ${MAX_WIN} windows open`}
        >
          {Array.from({ length: MAX_WIN }, (_, i) => (
            <span key={i} className={`ws-pip ${i < wins.length ? "on" : ""}`} />
          ))}
        </div>
        <span className="ws-flex" />
        <button
          className="ws-add"
          onClick={() => addWindow()}
          disabled={wins.length >= MAX_WIN}
          title={wins.length >= MAX_WIN ? "four windows max" : "add a window"}
        >
          <span className="ws-add-plus">+</span> window
        </button>
        {wins.length > 0 && (
          <button className="ws-btn" onClick={() => setWins([])} title="close every window">
            clear
          </button>
        )}
      </div>

      <div className="ws-body">
        <aside className={`ws-side ${side ? "" : "closed"}`}>
          <div className="ws-side-head">
            {side && <span className="ws-side-title">previously visited</span>}
            <button
              className="ws-icon"
              onClick={() => setSide((s) => !s)}
              title={side ? "collapse sidebar" : "expand sidebar"}
            >
              {side ? "«" : "»"}
            </button>
          </div>
          <div className="ws-side-list">
            {recentMods.map((m) => (
              <button
                key={m.name}
                className={`ws-side-item ${openApps.has(m.name) ? "open" : ""}`}
                onClick={() => addWindow(m.name)}
                title={side ? m.description : m.name}
              >
                <span
                  className="ws-app-ic"
                  style={{ background: m.color || hue(m.name) }}
                >
                  {(m.icon || m.name[0]).slice(0, 1)}
                </span>
                {side && <span className="ws-side-name">{m.name}</span>}
                {side && openApps.has(m.name) && <span className="ws-side-dot" />}
              </button>
            ))}
            {side && recentMods.length === 0 && (
              <div className="ws-side-empty">
                apps you visit show up here for quick reopening
              </div>
            )}
          </div>
        </aside>

        <div className={`ws-canvas ${dragMoved ? "dragging" : ""}`} ref={canvasRef}>
          {wins.length === 0 && (
            <button className="ws-empty" onClick={() => addWindow()}>
              <span className="ws-empty-plus">+</span>
              <span>Add a window</span>
              <span className="ws-empty-hint">up to four · drag to snap into quarters</span>
            </button>
          )}

          {/* snap target ghost — where the dragged window will land */}
          {dragMoved && drag && previewRects[drag.id] && (
            <div className="ws-ghost" style={rectStyle(previewRects[drag.id])} />
          )}

          {wins.map((w) => {
            const lifted = dragMoved && drag?.id === w.id;
            const rect = lifted ? baseRects[w.id] : previewRects[w.id];
            const style: React.CSSProperties =
              maximized === w.id
                ? {
                    top: GAP / 2,
                    left: GAP / 2,
                    width: `calc(100% - ${GAP}px)`,
                    height: `calc(100% - ${GAP}px)`,
                  }
                : rectStyle(rect);
            if (lifted && drag) {
              style.transform = `translate(${drag.dx}px, ${drag.dy}px) scale(0.98)`;
            }
            return (
              <Window
                key={w.id}
                win={w}
                style={style}
                lifted={lifted}
                maximized={maximized === w.id}
                mods={mods}
                nonce={nonces.current[w.id] || 0}
                picking={picking === w.id}
                onGrab={(e) => onGrab(e, w)}
                onDragMove={onDragMove}
                onDrop={onDrop}
                onPick={() => setPicking(w.id)}
                onCancelPick={() => setPicking(null)}
                onChoose={(n) => setApp(w.id, n)}
                onClose={() => closeWindow(w.id)}
                onReload={() => reloadWindow(w.id)}
                onToggleMax={() => setMaximized((m) => (m === w.id ? null : w.id))}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

// Emoji glyphs (⛶/🗗) tofu on some platforms — crisp inline SVGs instead.
function ExpandIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
      <path
        d="M4.2 1.5H1.5v2.7M7.8 1.5h2.7v2.7M4.2 10.5H1.5V7.8M7.8 10.5h2.7V7.8"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

function RestoreIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
      <rect
        x="1.5"
        y="3.8"
        width="6.7"
        height="6.7"
        rx="1.2"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
      />
      <path
        d="M4 1.7h5.2a1.1 1.1 0 0 1 1.1 1.1V8"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

function Window({
  win,
  style,
  lifted,
  maximized,
  mods,
  nonce,
  picking,
  onGrab,
  onDragMove,
  onDrop,
  onPick,
  onCancelPick,
  onChoose,
  onClose,
  onReload,
  onToggleMax,
}: {
  win: Win;
  style: React.CSSProperties;
  lifted: boolean;
  maximized: boolean;
  mods: Module[] | null;
  nonce: number;
  picking: boolean;
  onGrab: (e: React.PointerEvent<HTMLDivElement>) => void;
  onDragMove: (e: React.PointerEvent<HTMLDivElement>) => void;
  onDrop: () => void;
  onPick: () => void;
  onCancelPick: () => void;
  onChoose: (name: string) => void;
  onClose: () => void;
  onReload: () => void;
  onToggleMax: () => void;
}) {
  const name = win.app;
  const url = name ? gatewayUrl(name) : "";
  const showPicker = !name || picking;

  return (
    <section
      className={`ws-win ${lifted ? "lift" : ""} ${maximized ? "max" : ""}`}
      style={style}
    >
      <div
        className="ws-pane-bar"
        onPointerDown={onGrab}
        onPointerMove={onDragMove}
        onPointerUp={onDrop}
        onPointerCancel={onDrop}
      >
        <span className="ws-grip" aria-hidden>
          ⋮⋮
        </span>
        {name ? (
          <>
            <button className="ws-pane-name" onClick={onPick} title="switch app">
              <span className="ws-pane-dot" style={{ background: hue(name) }} />
              {name}
              <span className="ws-caret">▾</span>
            </button>
            <span className="ws-flex" />
            <button className="ws-icon" onClick={onReload} title="reload">
              ↻
            </button>
            <a
              className="ws-icon"
              href={url}
              target="_blank"
              rel="noreferrer"
              title="open in new tab"
            >
              ↗
            </a>
            <button
              className="ws-icon"
              onClick={onToggleMax}
              title={maximized ? "restore" : "expand"}
            >
              {maximized ? <RestoreIcon /> : <ExpandIcon />}
            </button>
            <button className="ws-icon close" onClick={onClose} title="close">
              ✕
            </button>
          </>
        ) : (
          <>
            <span className="ws-pane-name empty">new window</span>
            <span className="ws-flex" />
            <button className="ws-icon close" onClick={onClose} title="close">
              ✕
            </button>
          </>
        )}
      </div>

      <div className="ws-pane-body">
        {showPicker ? (
          <Picker
            mods={mods}
            current={name}
            onChoose={onChoose}
            onCancel={name ? onCancelPick : undefined}
          />
        ) : (
          <iframe
            key={nonce}
            src={url}
            className="ws-frame"
            title={`${name} app`}
            allow="clipboard-read; clipboard-write; fullscreen"
          />
        )}
      </div>
    </section>
  );
}

function Picker({
  mods,
  current,
  onChoose,
  onCancel,
}: {
  mods: Module[] | null;
  current: string | null;
  onChoose: (name: string) => void;
  onCancel?: () => void;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const gridRef = useRef<HTMLDivElement>(null);
  const recent = useMemo(() => recents.get(), []);

  // Searching ranks name-prefix > name > description; browsing shows recents
  // first, then everything alphabetically. One flat list keeps keyboard
  // selection simple across both sections.
  const { list, recentCount } = useMemo(() => {
    if (!mods) return { list: [] as Module[], recentCount: 0 };
    const s = q.trim().toLowerCase();
    if (s) {
      const rank = (m: Module) =>
        m.name.toLowerCase().startsWith(s)
          ? 0
          : m.name.toLowerCase().includes(s)
            ? 1
            : m.description.toLowerCase().includes(s)
              ? 2
              : 3;
      const list = mods
        .map((m) => [m, rank(m)] as const)
        .filter(([, r]) => r < 3)
        .sort((a, b) => a[1] - b[1] || a[0].name.localeCompare(b[0].name))
        .map(([m]) => m);
      return { list, recentCount: 0 };
    }
    const rec = recent
      .map((n) => mods.find((m) => m.name === n))
      .filter((m): m is Module => !!m)
      .slice(0, 6);
    const recSet = new Set(rec.map((m) => m.name));
    const rest = mods
      .filter((m) => !recSet.has(m.name))
      .sort((a, b) => a.name.localeCompare(b.name));
    return { list: [...rec, ...rest], recentCount: rec.length };
  }, [mods, q, recent]);

  const selIdx = Math.min(sel, Math.max(0, list.length - 1));

  useEffect(() => {
    gridRef.current
      ?.querySelector(`[data-idx="${selIdx}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selIdx]);

  function onKey(e: React.KeyboardEvent) {
    if (!list.length) {
      if (e.key === "Escape" && onCancel) onCancel();
      return;
    }
    // resolved template gives the live column count for up/down moves
    const cols = gridRef.current
      ? getComputedStyle(gridRef.current).gridTemplateColumns.split(" ").length
      : 1;
    const step: Record<string, number> = {
      ArrowRight: 1,
      ArrowLeft: -1,
      ArrowDown: cols,
      ArrowUp: -cols,
    };
    if (e.key in step) {
      e.preventDefault();
      setSel(Math.max(0, Math.min(list.length - 1, selIdx + step[e.key])));
    } else if (e.key === "Enter") {
      e.preventDefault();
      onChoose(list[selIdx].name);
    } else if (e.key === "Escape" && onCancel) {
      onCancel();
    }
  }

  const tile = (m: Module, i: number) => (
    <button
      key={m.name}
      className={`ws-app ${i === selIdx ? "sel" : ""} ${current === m.name ? "on" : ""}`}
      data-idx={i}
      onClick={() => onChoose(m.name)}
      onMouseEnter={() => setSel(i)}
      title={m.description}
    >
      <span className="ws-app-ic" style={{ background: m.color || hue(m.name) }}>
        {(m.icon || m.name[0]).slice(0, 1)}
      </span>
      <span className="ws-app-tx">
        <span className="ws-app-nm">{m.name}</span>
        <span className="ws-app-ds">{m.description}</span>
      </span>
    </button>
  );

  return (
    <div className="ws-picker">
      <div className="ws-picker-head">
        <input
          autoFocus
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setSel(0);
          }}
          onKeyDown={onKey}
          placeholder={mods ? "search apps to open…" : "loading apps…"}
          spellCheck={false}
        />
        {mods && (
          <span className="ws-picker-count">
            {list.length} app{list.length === 1 ? "" : "s"}
          </span>
        )}
        {onCancel && (
          <button className="ws-btn" onClick={onCancel}>
            cancel
          </button>
        )}
      </div>
      <div className="ws-picker-grid" ref={gridRef}>
        {recentCount > 0 && <div className="ws-picker-sec">recent</div>}
        {list.slice(0, recentCount).map((m, i) => tile(m, i))}
        {recentCount > 0 && <div className="ws-picker-sec">all apps</div>}
        {list.slice(recentCount).map((m, i) => tile(m, i + recentCount))}
        {mods && list.length === 0 && (
          <div className="ws-picker-empty">no apps match “{q.trim()}”</div>
        )}
      </div>
      <div className="ws-picker-foot">↑↓←→ navigate · ↵ open{onCancel ? " · esc cancel" : ""}</div>
    </div>
  );
}
