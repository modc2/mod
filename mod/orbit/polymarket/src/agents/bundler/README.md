# BUNDLER ◆

Forges copy-trade **bundles** — 3–5 traders copied together at equal dollars —
chosen for a high probability of *consistent* success, and proves every one on
days it never looked at.

## How a run works

1. **Pool** — every trader in the hub worker's feed store with enough history
   (no network; the fetch loop keeps it warm). Capped at the 400 most active.
2. **Blocks** — each trader is replayed through the real copy engine
   (`lib/basketSim.ts` → `runBacktest`, same fees/floors/late-fill model as the
   desk) in 10 blocks of 3 days, as if you started copying at each block's
   open with fresh cash.
3. **Train / holdout** — the 3 most recent blocks (9 days) are HOLDOUT. The
   screen, the greedy bundle search and the ranking read the other 7 only.
4. **Search** — greedy forward selection from several seeds maximizing the
   bundle objective (Wilson lower bound on P(green block) ×100, + smoothness,
   + a small return term, − tail penalty past −5%), skipping legs whose
   markets overlap > 50% with one already in. Every leg must ALSO be
   consistent on its own (Wilson LB ≥ 0.4): interleaving pairs of
   inconsistent traders look perfect in train and don't repeat.
5. **Prove** — each bundle is replayed once, continuously, over the holdout.
   Grade: **A** consistent · **B** promising · **C** unproven · **F** failed
   holdout. Ranking is grade then TRAIN objective — never the holdout number.
6. **Review** — a no-tool claude turn (`POLYMARKET_BUNDLER_MODEL`, default
   claude-sonnet-5) names each bundle, writes the thesis and risks, and gives
   deploy/watch/skip. It cannot change legs or numbers; an F can't be "deploy".

7. **Screen check** — each run publishes `skill`: the share of top-20 kept
   traders, other kept, and rejected traders that were up over the holdout.
   Reporting only. If top stops beating rejected, that run's bundles are
   unproven whatever their grade (first run 10-19: 70% / 55% / 31%).

## Surfaces

- Console: `/copytrade` ▸ **◆ BUNDLES** — FORGE NOW, grades, LOAD → studio.
- Route: `GET/POST /polymarket/_api/bundler` (`?run=1`, `?id=`), owner-gated.
- MCP: `pm_bundles` (op=status|latest|get|run|on|off). `run`/`on` are gated
  as spend under the desk chat.
- `python3 -m agents.bundler.mod on|off|toggle|info`

State: `~/.mod/polymarket/bundler/{settings.json,runs.json}`. The loop is OFF
by default (minutes of CPU + one model turn per run); default interval 6h.

## Honest limits

- The feed is capped at 30 days, so a bundle's evidence is ≤10 blocks. Small
  samples are why the score is a Wilson *lower* bound.
- The pool itself is "traders the worker watches" — chosen off current
  leaderboards, which include the holdout days. Selection *within* the pool
  is clean; pool membership is a mild leak.
- Resolutions and current marks value past inventory (same as every
  walk-forward here) — they score decisions, they don't make them.
