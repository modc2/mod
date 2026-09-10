"""ui/compress eval - can the agent compress a toolbar without losing it.

The design question every console hits eventually: three permanent buttons say
one thing between them, and the rare two are paying rent on the hot path. The
fix is to fold them into one control — but a fold that drops the keyboard, the
menu semantics, or one of the three ops is not a fix, it is a regression that
happens to be smaller.

So the fixture is a real, already-folded control (face + caret + portaled
menu) and the task is to make it *read* better without unfolding it. Scoring
is on the invariants a fold must keep, not on taste: the three ops are all
still reachable, the menu is still a menu to a screen reader, arrow keys still
work, and the menu is still portaled (the header carries a backdrop-filter,
which makes it the containing block for fixed children — an in-place menu
lands at the wrong offset).

Taste is then read off the board: every agent faces the same fixture under the
same budget, the invariants gate the score, and the surviving candidates are
compared by what they actually wrote.
"""

FIXTURE = '''"use client";

// EDIT | FORK | NEW as ONE control: the face fires the op you used last, the
// caret opens the other two.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { EditIcon, ForkIcon, NewIcon } from "./Icons";

export type ModuleOp = "edit" | "fork" | "new";

const OPS = [
  { op: "edit", Icon: EditIcon, label: "edit", color: "#60a5fa", hint: (m: string) => `change ${m}` },
  { op: "fork", Icon: ForkIcon, label: "fork", color: "#fbbf24", hint: (m: string) => `copy ${m} into a new module` },
  { op: "new", Icon: NewIcon, label: "new", color: "#4ade80", hint: () => "scaffold a brand-new module" },
] as const;

type Props = {
  activeMode: string;
  active: boolean;
  onPick: (op: ModuleOp) => void;
  modName: string;
};

export function ModuleOps({ activeMode, active, onPick, modName }: Props) {
  const [op, setOp] = useState<ModuleOp>("edit");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (activeMode === "edit" || activeMode === "fork" || activeMode === "new") setOp(activeMode);
  }, [activeMode]);

  const current = OPS.find((o) => o.op === op) || OPS[0];
  const lit = active && activeMode === op;

  const place = useCallback(() => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (r) setAnchor({ x: r.left, y: r.bottom + 6 });
  }, []);

  const pick = (next: ModuleOp) => {
    setOp(next);
    setOpen(false);
    onPick(next);
  };

  return (
    <>
      <div ref={wrapRef} className="flex items-stretch overflow-hidden"
        style={{ height: 28, borderRadius: 999, color: current.color,
                 background: `${current.color}${lit ? "26" : "14"}`,
                 border: `1px solid ${current.color}40` }}>
        <button onClick={() => onPick(op)} title={`${current.label.toUpperCase()} - ${current.hint(modName)}`}
          aria-label={`${current.label} module`} aria-pressed={lit}
          style={{ width: 26, background: "transparent", border: "none", color: "inherit", cursor: "pointer" }}>
          <current.Icon size={15} />
        </button>
        <button onClick={() => { place(); setOpen(!open); }}
          onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); place(); setOpen(true); } }}
          title="edit / fork / new" aria-label="Pick module action"
          aria-haspopup="menu" aria-expanded={open}
          style={{ width: 15, fontSize: 9, background: "transparent", border: "none",
                   borderLeft: `1px solid ${current.color}33`, color: "inherit", cursor: "pointer" }}>
          <span aria-hidden="true">v</span>
        </button>
      </div>

      {open && anchor && createPortal(
        <div role="menu" tabIndex={-1}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
            else if (e.key === "ArrowDown") setCursor((c) => (c + 1) % OPS.length);
            else if (e.key === "ArrowUp") setCursor((c) => (c - 1 + OPS.length) % OPS.length);
            else if (e.key === "Enter") pick(OPS[cursor].op);
          }}
          className="fixed z-[201] rounded-xl py-1"
          style={{ left: anchor.x, top: anchor.y, minWidth: 240, background: "var(--bg-primary)",
                   border: "1px solid var(--border-color)" }}>
          {OPS.map((o, i) => (
            <div key={o.op} role="menuitem" onClick={() => pick(o.op)} onMouseEnter={() => setCursor(i)}
              className="px-2.5 py-1.5 flex items-center gap-2"
              style={{ background: i === cursor ? `${o.color}1a` : "transparent" }}>
              <o.Icon size={14} />
              <span className="text-[11px] font-bold uppercase">{o.label}</span>
              <span className="text-[10px] opacity-60">{o.hint(modName)}</span>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}

export default ModuleOps;
'''


PROMPT = (
    "Your working directory is {workdir}. It contains ModuleOps.tsx, one React "
    "control that folds three module actions (edit, fork, new) into a single "
    "split pill: the face fires the op you used last, the caret opens a menu "
    "with the other two.\n\n"
    "It works, but it reads badly: 26 icon-only pixels in a header full of "
    "words, so a stranger cannot tell what it does or what it will do next, "
    "and there is no sign the caret holds anything.\n\n"
    "Rewrite {workdir}/ModuleOps.tsx so the control TELLS you what it is "
    "without getting bigger than a chip. You may add a text label, a hover or "
    "focus affordance, better tooltips, colour, whatever you can defend.\n\n"
    "Hard constraints — a rewrite that breaks any of these is worse than no "
    "rewrite:\n"
    "  1. It stays ONE control. Do not go back to three buttons side by side.\n"
    "  2. All three ops (edit, fork, new) stay reachable, and clicking the "
    "face still fires the current op in one click.\n"
    "  3. The menu keeps role=\"menu\", aria-haspopup and aria-expanded, and "
    "still moves on ArrowDown / ArrowUp and closes on Escape.\n"
    "  4. The menu stays portaled to document.body via createPortal — the "
    "header has a backdrop-filter, so an in-place menu lands at the wrong "
    "offset.\n"
    "  5. It stays valid TypeScript React: the file still exports ModuleOps "
    "and the ModuleOp type.\n\n"
    "Write the whole file back to {workdir}/ModuleOps.tsx, then finish."
)


class Eval:
    name = "ui/compress"
    description = "Fold a toolbar into one control without losing the ops, the keyboard, or the menu."
    language = "typescript"
    owner = None
    agents = None  # every subject

    tasks = [
        {
            "title": "make the folded control legible",
            "prompt": PROMPT,
            "steps": 12,
            "setup": {"files": {"ModuleOps.tsx": FIXTURE}},
            "scorers": [
                # it wrote something back
                {"type": "file_exists", "path": "ModuleOps.tsx"},
                # all three ops survived the edit
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'"edit"'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'"fork"'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'"new"'},
                # still a menu to a screen reader
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'aria-haspopup'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'aria-expanded'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'role=\{?"menu"'},
                # still keyboard-driven
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'ArrowDown'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'ArrowUp'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'Escape'},
                # still portaled out of the backdrop-filtered header
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'createPortal'},
                # still the same component
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'export\s+(function|const)\s+ModuleOps|export\s+default\s+ModuleOps'},
                {"type": "file_regex", "path": "ModuleOps.tsx", "pattern": r'ModuleOp\b'},
                # ...and it actually did something. Every check above is
                # satisfied by handing the fixture straight back, which is how
                # the first round scored 0.99 for a byte-identical no-op.
                {"type": "file_not_contains", "path": "ModuleOps.tsx", "text": FIXTURE},
            ],
        },
    ]
