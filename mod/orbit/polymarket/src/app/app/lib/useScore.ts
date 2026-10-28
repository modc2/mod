"use client";

// One hook behind every SCORE box — the /traders board and the copy desk's
// ADD A TRADER panel both rank on it. Give it the text of the box and it
// hands back a synchronous scoreFor covering all three languages: plain
// expressions and JS compile inline; Python compiles through Pyodide (async,
// debounced so half-typed defs don't spam the compiler) and lands as state.
//
// scoreFor(t) returns the trader's score — or NULL when the user's function
// filtered that trader out. While a Python function is still compiling,
// every score is -Infinity: order holds steady instead of flickering.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { TopTrader } from "./polymarket";
import {
  compileScore, detectScoreLang, scoreInputs,
  type CompiledScore, type ScoreLang,
} from "./scoreFormula";
import { compilePyScore } from "./pyScore";

export interface ScoreState {
  /** fn + error for the CURRENT text (a stale Python compile never leaks). */
  compiled: CompiledScore;
  lang: ScoreLang;
  /** True while the Python runtime/compile for the current text is pending. */
  loading: boolean;
  scoreFor: (t: TopTrader) => number | null;
}

export function useCompiledScore(formula: string): ScoreState {
  const lang = detectScoreLang(formula);
  const sync = useMemo<CompiledScore>(
    () => (lang === "py" ? { fn: null, error: null } : compileScore(formula)),
    [formula, lang],
  );

  const [pyDone, setPyDone] = useState<{ src: string; compiled: CompiledScore } | null>(null);
  useEffect(() => {
    if (lang !== "py") return;
    let dead = false;
    const t = setTimeout(() => {
      compilePyScore(formula).then(
        (compiled) => { if (!dead) setPyDone({ src: formula, compiled }); },
        (e) => { if (!dead) setPyDone({ src: formula, compiled: { fn: null, error: String(e) } }); },
      );
    }, 400);
    return () => { dead = true; clearTimeout(t); };
  }, [formula, lang]);

  const compiled = lang === "py"
    ? (pyDone && pyDone.src === formula ? pyDone.compiled : { fn: null, error: null })
    : sync;
  const loading = lang === "py" && (!pyDone || pyDone.src !== formula);

  const scoreFor = useCallback(
    (t: TopTrader): number | null =>
      compiled.fn ? compiled.fn(scoreInputs(t)) : Number.NEGATIVE_INFINITY,
    [compiled],
  );

  return { compiled, lang, loading, scoreFor };
}
