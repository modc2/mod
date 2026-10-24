"use client";

// The SCORE preset row, with the user's own ratios on it.
//
// Builtin presets (WIN RATE, EXIT/ENTRY, SHARPE) and saved ratios are the
// same thing — a named formula rendered as a chip — except the user writes
// the second kind: type any expression (pnl / volume), press SAVE, name it,
// and it becomes a chip on every leaderboard. The active saved chip grows an
// ✕ so a ratio can be retired from where it's used. The list lives in
// localStorage via scoreFormula.ts, shared by /traders and the copy desk.

import { useEffect, useState } from "react";

import {
  SCORE_PRESETS, type SavedRatio,
  addSavedRatio, loadSavedRatios, matchSavedRatio, matchScorePreset,
  removeSavedRatio, suggestRatioName,
} from "../lib/scoreFormula";

interface Props {
  formula: string;
  setFormula: (f: string) => void;
  /** Whether the current formula compiles — SAVE hides on a broken one. */
  canSave: boolean;
  /** Chip classes shared by every state — the two boards size differently. */
  btnClass: string;
  /** Extra classes for a chip that is NOT the active formula. Kept separate
      because Tailwind precedence is stylesheet order: text-pixel-gray in the
      base would fight the active text-pixel-green. */
  idleClass?: string;
}

const ACTIVE = "border-pixel-green text-pixel-green";

export default function ScoreRatioChips({ formula, setFormula, canSave, btnClass, idleClass = "" }: Props) {
  const [ratios, setRatios] = useState<SavedRatio[]>([]);
  useEffect(() => { setRatios(loadSavedRatios()); }, []);

  // The SAVE flow: a button that becomes a name input.
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");

  const activePreset = matchScorePreset(formula);
  const activeRatio = matchSavedRatio(formula, ratios);
  const chip = (isActive: boolean) => `${btnClass} ${isActive ? ACTIVE : idleClass}`;

  const commit = () => {
    const n = name.trim();
    if (!n) return;
    setRatios(addSavedRatio(ratios, n, formula.trim()));
    setNaming(false);
  };

  return (
    <>
      {SCORE_PRESETS.map((p) => (
        <button
          key={p.key}
          className={chip(activePreset?.key === p.key)}
          onClick={() => setFormula(p.formula)}
          title={p.hint}
        >
          {p.label}
        </button>
      ))}
      {ratios.map((r) => (
        <span key={r.name} className="inline-flex items-center gap-0.5">
          <button
            className={chip(activeRatio?.name === r.name)}
            onClick={() => setFormula(r.formula)}
            title={`Your ratio: ${r.formula}`}
          >
            {r.name}
          </button>
          {activeRatio?.name === r.name && (
            <button
              className={`${btnClass} px-1 text-red-400/80 hover:text-red-400`}
              onClick={() => setRatios(removeSavedRatio(ratios, r.name))}
              title={`Delete ${r.name} — the formula stays in the box`}
            >
              ✕
            </button>
          )}
        </span>
      ))}
      {canSave && !activePreset && !activeRatio && formula.trim() !== "" && (
        naming ? (
          <span className="inline-flex items-center gap-1">
            <input
              autoFocus
              className="pixel-input-sm w-28 font-mono text-[11px]"
              value={name}
              spellCheck={false}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setNaming(false);
              }}
              title="Name this ratio — Enter saves, Esc cancels"
            />
            <button className={chip(false)} onClick={commit} title="Save as a chip">
              &#10003;
            </button>
            <button className={chip(false)} onClick={() => setNaming(false)} title="Cancel">
              ✕
            </button>
          </span>
        ) : (
          <button
            className={`${chip(false)} border-dashed`}
            onClick={() => { setName(suggestRatioName(formula)); setNaming(true); }}
            title={`Save "${formula.trim()}" as your own preset chip — it'll show on both leaderboards`}
          >
            + SAVE
          </button>
        )
      )}
    </>
  );
}
