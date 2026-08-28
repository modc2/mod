"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * The console's one hover bubble.
 *
 * Mount it once (layout.tsx) and every `title=` in the app gets a themed
 * tooltip instead of OS chrome — gray Helvetica bubbles read as a browser
 * dialog dropped on a CRT, arrive a second late, and clip multi-line hints to
 * whatever the platform feels like. Nothing at the call site changes: the
 * layer reads `title` (or `data-tip`, for the header chips that were already
 * hand-rolled), borrows the native attribute while the pointer is on the
 * element, and hands it back on the way out so the accessible name survives.
 *
 * It also does what the CSS-pseudo-element version couldn't: flips above the
 * anchor near the bottom edge, clamps inside the viewport with the caret still
 * pointing at what you're hovering, and wraps long text instead of ellipsing.
 */

/** Anchors that own a hint. `data-tip` wins when an element carries both. */
const TIP_SELECTOR = "[data-tip], [title]";
/** An iframe's title names the frame for screen readers — never a hover hint. */
const SKIP_TAGS = new Set(["IFRAME", "OPTION"]);

const SHOW_DELAY = 150;   // hover intent, so a swipe across the header stays quiet
const GRACE = 400;        // …but once a tip is up, sliding to the next one is instant
const GAP = 8;            // anchor → bubble
const EDGE = 8;           // keep the bubble this far inside the viewport
const CARET_INSET = 13;   // how close the caret may get to a bubble corner

type Anchor = { el: HTMLElement; text: string };
type Place = { x: number; y: number; caret: number; above: boolean };

export default function Tooltip() {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [place, setPlace] = useState<Place | null>(null);
  const bubble = useRef<HTMLDivElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Mirror of `anchor` — the pointer handler must not re-bind on every hover. */
  const shown = useRef<HTMLElement | null>(null);
  /** Element whose `title` we borrowed, waiting to be handed back. */
  const borrowed = useRef<{ el: HTMLElement; title: string } | null>(null);
  const lastHidden = useRef(0);

  const restoreTitle = useCallback(() => {
    const b = borrowed.current;
    borrowed.current = null;
    if (b && !b.el.hasAttribute("title")) b.el.setAttribute("title", b.title);
  }, []);

  const hide = useCallback(() => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    restoreTitle();
    if (shown.current) lastHidden.current = Date.now();
    shown.current = null;
    setAnchor(null);
    setPlace(null);
  }, [restoreTitle]);

  // Measure, then place. The bubble renders hidden for one frame so its real
  // size decides which side of the anchor it lands on.
  useLayoutEffect(() => {
    if (!anchor || !bubble.current) return;
    if (!anchor.el.isConnected) { hide(); return; }
    const a = anchor.el.getBoundingClientRect();
    const b = bubble.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const under = a.bottom + GAP;
    const over = a.top - GAP - b.height;
    const flip = under + b.height > vh - EDGE && over >= EDGE;

    const center = a.left + a.width / 2;
    const x = Math.min(Math.max(center - b.width / 2, EDGE), Math.max(EDGE, vw - EDGE - b.width));
    const caret = Math.min(Math.max(center - x, CARET_INSET), Math.max(CARET_INSET, b.width - CARET_INSET));

    setPlace({ x: Math.round(x), y: Math.round(flip ? over : under), caret: Math.round(caret), above: flip });
  }, [anchor, hide]);

  // Rows re-render out from under the pointer (a task finishes, the rail
  // resorts). Without this the bubble would hang over the gap its anchor left.
  useEffect(() => {
    if (!anchor) return;
    const t = setInterval(() => { if (!anchor.el.isConnected) hide(); }, 250);
    return () => clearInterval(t);
  }, [anchor, hide]);

  useEffect(() => {
    const hint = (el: HTMLElement | null) =>
      el && !SKIP_TAGS.has(el.tagName)
        ? (el.getAttribute("data-tip") || el.getAttribute("title") || "").trim()
        : "";

    const show = (el: HTMLElement, text: string) => {
      // Borrow the native title for as long as we're drawing it ourselves, so
      // the OS bubble never gets its chance to fire.
      if (el.hasAttribute("title")) {
        restoreTitle();
        borrowed.current = { el, title: el.getAttribute("title") || "" };
        el.removeAttribute("title");
      }
      shown.current = el;
      setPlace(null);
      setAnchor({ el, text });
    };

    const onOver = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      const target = e.target as HTMLElement | null;
      const el = target?.closest?.(TIP_SELECTOR) as HTMLElement | null;
      const text = hint(el);
      if (!el || !text) { hide(); return; }
      if (shown.current === el) return;
      if (timer.current) clearTimeout(timer.current);
      // Reading one chip in a row means you're reading the row — skip the
      // intent delay while the previous tip is still warm.
      if (shown.current || Date.now() - lastHidden.current < GRACE) { show(el, text); return; }
      timer.current = setTimeout(() => show(el, text), SHOW_DELAY);
    };

    // Tab to a symbol-only chip and you get the same hint the mouse does —
    // `:focus-visible` keeps it off the buttons you merely clicked.
    const onFocus = (e: FocusEvent) => {
      const el = (e.target as HTMLElement | null)?.closest?.(TIP_SELECTOR) as HTMLElement | null;
      const text = hint(el);
      if (!el || !text || !el.matches(":focus-visible")) return;
      if (timer.current) clearTimeout(timer.current);
      show(el, text);
    };

    const onScroll = () => { if (shown.current || timer.current) hide(); };

    // Coarse pointers have no hover to speak of; a tap-triggered bubble would
    // just sit on top of the thing you tapped. Focus still counts, though —
    // that's a keyboard, whatever the screen is.
    if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
      document.addEventListener("pointerover", onOver, true);
      document.addEventListener("pointerdown", hide, true);
    }
    document.addEventListener("focusin", onFocus, true);
    document.addEventListener("focusout", hide, true);
    document.addEventListener("keydown", hide, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("blur", hide);
    window.addEventListener("resize", hide);
    return () => {
      document.removeEventListener("pointerover", onOver, true);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("focusin", onFocus, true);
      document.removeEventListener("focusout", hide, true);
      document.removeEventListener("keydown", hide, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("blur", hide);
      window.removeEventListener("resize", hide);
      hide();
    };
  }, [hide, restoreTitle]);

  if (!anchor) return null;

  return createPortal(
    <div
      ref={bubble}
      className="tip-bubble"
      role="tooltip"
      data-side={place?.above ? "above" : "below"}
      style={{
        transform: `translate3d(${place?.x ?? 0}px, ${place?.y ?? 0}px, 0)`,
        visibility: place ? "visible" : "hidden",
      }}
    >
      {anchor.text}
      <span className="tip-caret" style={{ left: place?.caret ?? 0 }} />
    </div>,
    document.body,
  );
}
