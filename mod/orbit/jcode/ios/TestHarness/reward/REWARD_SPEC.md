# jcode iOS UX Reward Framework

> Goal: turn "this looks ugly / inefficient" into a single, hill-climbable
> reward in [0, 100], decomposed into weighted categories, each backed by an
> objective scorer. A UI change is an improvement **only if it raises the
> weighted reward across the device x content matrix**, not one lucky screen.

## How reward is produced

```
screenshot(s) + source tree + (optional) AX tree / runtime traces
        |
        v   per category, independent scorer -> CategoryScore(0..100, evidence)
   [ scorers ]
        |
        v   weighted sum, normalized
   overall reward (0..100)  +  per-category breakdown  +  worst-cell callout
```

Everything runs headless on this machine via the existing harness
(`mock_gateway.py` scenarios + `xcrun simctl` screenshots). No LLM, no device,
no network.

## Category taxonomy (what matters, perceived UX first)

Weights sum to 1.0. The rebalance principle: what a real user of a mobile
coding-agent chat app *feels* every minute dominates. That is (1) reading
streaming text and tool output for long stretches -> legibility first,
(2) the tap-cost of frequent flows (send, interrupt, switch session) ->
ergonomics second. Abstract pixel-geometry aesthetics (fill ratios, grid snap,
focal-point math) matter but cannot outvote "can I read it, can I reach it".

### A. Space & density (weight 0.15) - "no wasted pixels"
- `space_efficiency` (0.05)  canvas fill ratio, vertical balance, largest dead
                       zone. Scenario-aware: the `empty` scenario is graded as
                       an empty STATE (calm canvas + visible start affordance),
                       never against the 30-60% transcript fill band.
- `information_density` (0.05) useful content vs chrome. Scenario-aware: on
                       `empty` it grades chrome leanness instead of transcript
                       ink share (an empty transcript is not "low density").
- `content_safety` (0.05) no clipping/overflow/truncation; nothing under chrome.

### B. Ergonomics & interaction (weight 0.30) - "cheap to use"
The dominant category: users touch this app dozens of times per session.
- `touch_targets` (0.10)   interactive elements >= 44x44pt, adequate spacing.
- `reachability` (0.08)    primary actions in the comfortable thumb zone.
- `interaction_cost` (0.12) expected seconds per action for the key flows
                       (send, interrupt, switch session, change model, pair),
                       KLM/Fitts model grounded in real usage logs.

### C. Visual clarity (weight 0.12) - "easy to parse"
- `visual_hierarchy` (0.04) one clear focal point / salient primary action.
                       Scenario-aware: on `empty`, a single obvious start
                       affordance is the whole job, so the concentration
                       target is relaxed (0.3 vs 0.5) and the one-focal-point
                       term dominates.
- `consistency` (0.04)     design-token discipline (source) + palette discipline
                       (pixel): few dominant colors, aligned margins.
- `rhythm` (0.04)          spacing snaps to the 8pt grid (source + pixel).

### D. Legibility & accessibility (weight 0.22) - "everyone can read it"
Highest-leverage for this product: sessions are spent READING streaming agent
text and tool output on a dark screen, often in bad light.
- `contrast` (0.14)        WCAG text/background contrast on real rendered
                       content, including the dim secondary/tool-output tier.
- `accessibility` (0.08)   VoiceOver labels, Dynamic Type, reduce-motion,
                       semantic roles present in source / AX tree.

### E. Responsiveness (weight 0.09) - "feels instant"
- `layout_robustness` (0.05) stable across the device x content matrix (variance
                       of per-cell scores; penalize fragile layouts).
- `perf` (0.04)            cold-launch-to-first-frame + scroll smoothness signals
                       (best-effort from simctl/Instruments; degrade to N/A).

### F. Design authenticity & craft (weight 0.12) - "designed, not generated"
Higher score = MORE crafted / LESS generic. Grounded in
`reward/AI_SLOP_RESEARCH.md` (documented AI-slop tells).
- `styling` (0.04)     aesthetic coherence & intent: one dominant color + sparing
                  sharp accent (not a timid rainbow), a real type scale, a
                  consistent radius/elevation system. Source + pixel.
- `simplicity` (0.04)  anti-complexity: shallow view-nesting depth, few distinct UI
                  primitives per screen, minimal card-in-card nesting, generous
                  purposeful negative space. Source + pixel.
- `ai_patterns` (0.04) anti-slop (higher = less slop): penalize purple/indigo/cyan
                  slop palettes, gradient text, glassmorphism/blur overuse,
                  generic fonts (Inter/Roboto/Arial/Space Grotesk), emoji-as-UI,
                  uniform 0.1 shadows, oversized uniform radius, decorative
                  badges. 0 tells = 100. Source primary, pixel corroboration.

## Scorer contract

Every scorer is a Python module under `TestHarness/reward/scorers/<name>.py`
exposing:

```python
NAME = "space_efficiency"      # unique id, matches taxonomy
CATEGORY = "A"                 # taxonomy group letter
WEIGHT = 0.12                  # relative weight within the framework

def score(ctx: "Context") -> "CategoryScore":
    "Pure function: read ctx, return a CategoryScore. No global mutation."
```

- `Context` (provided by `reward/context.py`) gives a scorer everything it may
  need: the screenshot path + decoded numpy array, the device + scenario, the
  px-per-point scale, the source root, an optional AX-tree JSON, and an
  optional runtime-metrics dict. Scorers use only what they need.
- Scorers SHOULD be scenario-aware via `ctx.scenario` when the scenario changes
  what a good screen looks like (e.g. `empty` is a deliberate empty state).
  Scenario-aware scorers report a `mode` key in evidence so per-cell reports
  stay auditable.
- `CategoryScore` (in `reward/types.py`): `{name, category, weight, value:
  0..100, evidence: dict, available: bool}`. `available=False` means "could not
  measure here" (e.g. perf without Instruments); the aggregator drops it and
  renormalizes weights so missing data never silently tanks the reward.
- Scorers must be deterministic and side-effect free. Determinism is enforced
  by `reward/test_determinism.py` (same input -> same output).

## Aggregation

`reward/aggregate.py`:
- discovers all scorer modules,
- runs each over every matrix cell (device x scenario),
- per cell: weighted mean of available categories (weights renormalized),
- overall: mean across cells, plus the single worst cell and worst category,
- emits a JSON report and a human table; supports
  `--baseline-json A --candidate-json B` for regression gating in CI.

## Why this design

- **Parallel-safe:** one file per scorer means swarm workers never edit the
  same file. The contract + `types.py`/`context.py` are the only shared API.
- **Honest:** scorers read rendered pixels / real source, not the view model,
  so they can't be gamed by lying state.
- **Hill-climbable:** the matrix mean is the objective; `--baseline/--candidate`
  makes every change a measurable +/- delta.
- **Extensible:** add a category by dropping in a module; the aggregator finds
  it automatically.
