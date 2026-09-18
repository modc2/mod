// Python score functions, compiled and run IN THE BROWSER via Pyodide.
//
// Why in the browser: the score already runs client-side (the JS formula
// always has), and shipping user-typed Python to the server would hand the
// API an arbitrary-code endpoint. Pyodide is the same trust model as the
// existing `new Function` path — the user's code runs in their own tab,
// sandboxed by wasm, and nothing leaves the machine.
//
// The runtime (~7 MB) is lazy: nothing loads until the score box actually
// holds a `def`. Once loaded it stays for the session, and a compiled
// function is called SYNCHRONOUSLY per trader (a JSON round-trip per call —
// microseconds against a few hundred rows).
//
// Ported from polymarket's lib/pyScore.ts; only the variable set differs.

import { FORMULA_VARS, PROBE_INPUTS, normalizeScoreReturn, type CompiledScore, type ScoreInputs } from "./scoreFormula";

const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";

interface PyodideLike {
  runPython: (code: string, opts?: { globals?: unknown }) => unknown;
  globals: { get: (name: string) => { (): PyNamespace } };
}
interface PyNamespace {
  get: (name: string) => ((json: string) => string) | undefined;
  destroy: () => void;
}

let pyodideP: Promise<PyodideLike> | null = null;

/** Load pyodide.js from CDN once, then boot the runtime once. A failed load
    clears the cache so the next compile retries instead of failing forever. */
function loadPyodideOnce(): Promise<PyodideLike> {
  if (pyodideP) return pyodideP;
  pyodideP = new Promise<PyodideLike>((resolve, reject) => {
    const w = window as unknown as { loadPyodide?: (o: { indexURL: string }) => Promise<PyodideLike> };
    if (w.loadPyodide) { w.loadPyodide({ indexURL: PYODIDE_BASE }).then(resolve, reject); return; }
    const s = document.createElement("script");
    s.src = `${PYODIDE_BASE}pyodide.js`;
    s.onload = () => {
      if (!w.loadPyodide) return reject(new Error("pyodide.js loaded but loadPyodide is missing"));
      w.loadPyodide({ indexURL: PYODIDE_BASE }).then(resolve, reject);
    };
    s.onerror = () => reject(new Error("could not fetch the Python runtime (CDN unreachable?)"));
    document.head.appendChild(s);
  });
  pyodideP.catch(() => { pyodideP = null; });
  return pyodideP;
}

/** True once the runtime is resident — lets the UI say "loading Python…"
    only when that's actually about to happen. */
export function pyRuntimeReady(): boolean {
  return !!(window as unknown as { pyodide?: unknown }).pyodide || false;
}

// Runs AFTER the user's code, in the same namespace. Finds their function
// (prefer `score`, else the first non-underscore def), reads its signature
// once, and exposes _mod_score_call(json) -> json for the JS side.
// Calling conventions, friendliest first:
//   def score(pnl, volume, **rest): …   → keyword args, any subset
//   def score(pnl, volume): …           → just the ones you named
//   def score(t): …                     → the whole thing as one dict
const ADAPTER = `
import inspect as _inspect, json as _json, math as _math

_fn = None
if "score" in globals() and callable(globals()["score"]):
    _fn = globals()["score"]
else:
    for _k, _v in list(globals().items()):
        if not _k.startswith("_") and _inspect.isfunction(_v):
            _fn = _v
            break
if _fn is None:
    raise ValueError("no function found — write one, e.g.  def score(pnl, volume): ...")

_params = _inspect.signature(_fn).parameters
_kw_all = any(_p.kind == _p.VAR_KEYWORD for _p in _params.values())
_names = [_n for _n, _p in _params.items()
          if _p.kind in (_p.POSITIONAL_OR_KEYWORD, _p.KEYWORD_ONLY)]
_known = [_n for _n in _names if _n in ${JSON.stringify([...FORMULA_VARS])}]

def _mod_score_call(vars_json):
    v = _json.loads(vars_json)
    if _kw_all:
        r = _fn(**v)
    elif _known:
        r = _fn(**{n: v[n] for n in _known})
    else:
        r = _fn(v)
    if r is None or r is False:
        return "null"
    if r is True:
        return "1"
    if isinstance(r, (int, float)) and _math.isfinite(r):
        return _json.dumps(float(r))
    return "null"
`;

/** Strip Pyodide's wall of traceback down to the line a human wants. */
function pyErrorMessage(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  const lines = msg.trimEnd().split("\n").filter((l) => l.trim());
  return lines[lines.length - 1] ?? "python error";
}

/** Compile a `def score(…)` into a synchronous ScoreFn. The user's code runs
    ONCE here (top level + a zero-probe); after that each trader is one call
    into the already-compiled function. */
export async function compilePyScore(src: string): Promise<CompiledScore> {
  let py: PyodideLike;
  try {
    py = await loadPyodideOnce();
  } catch (e) {
    return { fn: null, error: pyErrorMessage(e) };
  }
  const ns = py.globals.get("dict")();
  let call: ((json: string) => string) | undefined;
  try {
    py.runPython(src, { globals: ns });
    py.runPython(ADAPTER, { globals: ns });
    call = ns.get("_mod_score_call");
    if (!call) throw new Error("internal: adapter did not attach");
    // Zero-probe: a signature that can't be called at all (params that aren't
    // variables mixed with ones that are) should fail HERE, not silently
    // blank the board. A ZeroDivisionError on zeros is the function working.
    try {
      call(JSON.stringify(PROBE_INPUTS));
    } catch (probeErr) {
      if (/TypeError/.test(String(probeErr))) throw probeErr;
    }
  } catch (e) {
    ns.destroy();
    return { fn: null, error: pyErrorMessage(e) };
  }
  const bound = call;
  return {
    fn: (t: ScoreInputs) => {
      try {
        return normalizeScoreReturn(JSON.parse(bound(JSON.stringify(t))));
      } catch {
        // A function that throws on a trader filters them — same contract as
        // returning None.
        return null;
      }
    },
    error: null,
  };
}
