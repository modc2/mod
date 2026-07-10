"use client";

import { useEffect, useRef, useState } from "react";

// A module's "image" is its running app, rendered live. The preview alternates
// between two device modes: a desktop frame scaled down to fill the container
// (reads like a real screenshot), and a phone frame — the same iframe resized
// to a phone viewport inside a bezel over a glow backdrop, so the app's actual
// responsive mobile layout shows. One iframe is reused across modes (resized,
// never remounted), so swapping costs a reflow, not a reload. Iframes are
// lazy-mounted (IntersectionObserver) so the 60-odd modules in the grid never
// all wake at once. A colored monogram stands in until the frame paints.

const DESKTOP = { w: 1280, h: 800 };
const PHONE = { w: 390, h: 844 };
const PHONE_BEZEL = 18;
const SWAP_MS = 8000; // dwell time per mode
const FADE_MS = 340; // fade-through while the iframe reflows

type Mode = "desktop" | "phone";

export function LivePreview({
  url,
  label,
  glow,
  interactive = false,
  onOpen,
  className = "",
}: {
  url: string;
  label: string;
  glow: string;
  /** When true the iframe receives pointer events (the hero); cards stay inert. */
  interactive?: boolean;
  /** Optional overlay action — used by the hero to jump into the App tab. */
  onOpen?: () => void;
  className?: string;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [box, setBox] = useState({ w: 320, h: 200 });
  const [mode, setMode] = useState<Mode>("desktop");
  const [pinned, setPinned] = useState<Mode | null>(null);
  const [swapping, setSwapping] = useState(false);

  // Mount the frame only once it scrolls near the viewport.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // Track the container size so each device frame fits it exactly.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (w && h) setBox({ w, h });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Alternate desktop ⇄ phone once the app has painted: fade out, swap the
  // viewport (the iframe reflows while invisible), fade back in. A little
  // jitter per cycle keeps a grid of cards from flipping in lockstep.
  useEffect(() => {
    if (!loaded || failed || pinned) return;
    let alive = true;
    let t = 0;
    const cycle = () => {
      t = window.setTimeout(
        () => {
          if (!alive) return;
          setSwapping(true);
          t = window.setTimeout(() => {
            if (!alive) return;
            setMode((m) => (m === "desktop" ? "phone" : "desktop"));
            setSwapping(false);
            cycle();
          }, FADE_MS);
        },
        SWAP_MS + Math.random() * 2500,
      );
    };
    cycle();
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [loaded, failed, pinned]);

  // If the app is asleep/blocked and never paints, fall back to the monogram.
  useEffect(() => {
    if (!visible || loaded) return;
    const t = window.setTimeout(() => setFailed(true), 12000);
    return () => window.clearTimeout(t);
  }, [visible, loaded]);

  const pick = (m: Mode) => {
    setSwapping(false);
    if (pinned === m) {
      setPinned(null); // second click resumes auto-alternating
    } else {
      setPinned(m);
      setMode(m);
    }
  };

  // Device geometry in container coordinates. Desktop pins to the top-left and
  // fills the width (cropping below the fold, like a screenshot); the phone is
  // centered and scaled to fit with a little breathing room.
  const bezel = mode === "phone" ? PHONE_BEZEL : 0;
  const vp = mode === "phone" ? PHONE : DESKTOP;
  const devW = vp.w + bezel * 2;
  const devH = vp.h + bezel * 2;
  const scale =
    mode === "phone"
      ? Math.min((box.h * 0.88) / devH, (box.w * 0.88) / devW)
      : box.w / devW;
  const x = mode === "phone" ? (box.w - devW * scale) / 2 : 0;
  const y = mode === "phone" ? (box.h - devH * scale) / 2 : 0;

  const ready = loaded && !failed;
  const showFallback = failed || !loaded;

  return (
    <div
      ref={wrapRef}
      className={`live-preview ${mode}${ready ? " ready" : ""}${swapping ? " swapping" : ""} ${className}`}
      style={{ "--glow": glow } as React.CSSProperties}
    >
      {showFallback && (
        <div className="live-preview-fallback" style={{ background: glow }}>
          <span>{label}</span>
        </div>
      )}

      <div className="live-preview-backdrop" />

      {visible && !failed && (
        <div
          className={`live-preview-device${mode === "phone" ? " phone" : ""}`}
          style={{
            width: devW,
            height: devH,
            transform: `translate(${x}px, ${y}px) scale(${scale})`,
          }}
        >
          <iframe
            src={url}
            title="live app preview"
            loading="lazy"
            scrolling="no"
            className="live-preview-frame"
            style={{
              top: bezel,
              left: bezel,
              width: vp.w,
              height: vp.h,
              pointerEvents: interactive ? "auto" : "none",
            }}
            onLoad={() => setLoaded(true)}
          />
        </div>
      )}

      {ready && (
        <div className="live-preview-dots" aria-hidden>
          <span className={mode === "desktop" ? "on" : ""} />
          <span className={mode === "phone" ? "on" : ""} />
        </div>
      )}

      {onOpen && ready && (
        <div className="live-preview-modes">
          {(["desktop", "phone"] as const).map((m) => (
            <button
              key={m}
              type="button"
              className={mode === m ? "on" : ""}
              onClick={() => pick(m)}
              title={
                pinned === m
                  ? "Pinned — click to resume auto-alternating"
                  : `Pin ${m} view`
              }
            >
              {m}
              {pinned === m ? " ●" : ""}
            </button>
          ))}
        </div>
      )}

      {onOpen && (
        <button
          type="button"
          className="live-preview-open"
          onClick={onOpen}
          title="Open the interactive app"
        >
          ▶ play
        </button>
      )}
    </div>
  );
}
