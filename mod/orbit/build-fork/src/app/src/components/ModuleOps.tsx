"use client";

// EDIT · FORK · NEW as three bare symbols.
//
// This was a split pill for a while: the face fired whichever op you used
// last, a caret held the other two. It saved room, but it cost a click on two
// of three ops and, worse, the button's meaning moved — what the face did
// depended on what you did last, so you had to read it before clicking it.
//
// Three targets now, always in the same order, always in the same place, each
// one click. No box around them and no dividers between them: the strip is
// already a row of bordered boxes (the name chip, the tabs, the account chip)
// and a fourth one just to fence three 15px glyphs was chrome the glyphs were
// paying for. They're held together by proximity and by sharing the row's
// centre line instead. Only the op the composer is currently aimed at gets a
// tinted disc, so the lit one is the one piece of chrome in the group and it
// means something.
//
// Icons only: the words are on the tooltip and the aria-label, where a lone
// glyph would otherwise be a guess. And no menu, so no portal, no focus
// return, no measured rect — the header strip carries a backdrop-filter and
// anything `fixed` inside it lands at the wrong offset, which is a whole
// class of bug this control no longer has.

import { EditIcon, ForkIcon, NewIcon } from "./Icons";

export type ModuleOp = "edit" | "fork" | "new";

const OPS = [
  { op: "edit", Icon: EditIcon, label: "edit", color: "#60a5fa", hint: (m: string) => `change ${m}` },
  { op: "fork", Icon: ForkIcon, label: "fork", color: "#fbbf24", hint: (m: string) => `copy ${m} into a new module` },
  { op: "new", Icon: NewIcon, label: "new", color: "#4ade80", hint: () => "scaffold a brand-new module" },
] as const;

type Props = {
  /** The composer's current tool — the matching segment lights up while it's
   *  one of the three (the composer's own chip can also set git/cid, which
   *  have no segment here; nothing is lit then). */
  activeMode: string;
  /** Composer open and aimed at `activeMode` — lights the segment up. */
  active: boolean;
  onPick: (op: ModuleOp) => void;
  modName: string;
  isMobile?: boolean;
};

export function ModuleOps({ activeMode, active, onPick, modName, isMobile = false }: Props) {
  const h = isMobile ? 32 : 28;

  return (
    <div className="omni-op shrink-0 flex items-center" style={{ height: h, gap: isMobile ? 2 : 1, lineHeight: 1 }}>
      {OPS.map((o) => {
        const lit = active && activeMode === o.op;
        return (
          <button
            key={o.op}
            onClick={() => onPick(o.op)}
            className="omni-op__btn flex items-center justify-center transition-all"
            style={{
              width: h,
              height: h,
              borderRadius: 999,
              // The aimed op is the only one wearing a disc. The other two are
              // just their own colour, held back a step so the lit one leads
              // without any of the three going quiet enough to hunt for.
              background: lit ? `${o.color}24` : "transparent",
              border: `1px solid ${lit ? `${o.color}66` : "transparent"}`,
              // Idle glyphs are mixed toward the theme's own text colour
              // rather than just faded: a flat 0.6 opacity reads fine on the
              // dark skins and washes amber-on-cream out to nothing on the
              // light ones. The mix darkens on light bases and lightens on
              // dark ones, so all three stay legible on all seventeen.
              color: lit ? o.color : `color-mix(in srgb, ${o.color} 68%, var(--text-secondary))`,
              opacity: lit ? 1 : 0.85,
              cursor: "pointer",
            }}
            title={`${o.label.toUpperCase()} — ${o.hint(modName)}`}
            aria-label={`${o.label} module`}
            aria-pressed={lit}
          >
            <o.Icon size={isMobile ? 16 : 15} />
          </button>
        );
      })}
    </div>
  );
}

export default ModuleOps;
