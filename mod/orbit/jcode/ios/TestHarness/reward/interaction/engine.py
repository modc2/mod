"""engine.py - price the user-behavior graph into interaction-cost metrics.

Given the weighted ActionGraph (user_model) and target geometry (ui_map), this:

  1. Normalizes each state's out-edge weights into transition probabilities,
     forming a Markov chain over UI states.
  2. Solves for the stationary distribution pi (how often a user occupies each
     state over a long session) via power iteration.
  3. Prices every action in SECONDS with the KLM/TLM + Fitts cost_model, tracking
     the previous target so repeat-tap Fitts movement is charged correctly.
  4. Produces:
       - expected_action_cost_s: the expected seconds per action, weighting each
         action by pi[state] * P(action | state). This is the headline number:
         the average cost of a thing the user does, in real seconds.
       - task_times: completion time per canonical Task (sum of its action costs),
         and a frequency-weighted mean task time.
       - per_state and per_action detail for debugging / optimization targeting.

Lower seconds = better. To feed the reward (higher=better), call
reward_score(), which maps expected cost through a calibrated curve to 0..100.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from reward.interaction.cost_model import action_cost
from reward.interaction.model import Action, ActionGraph, Operators, UITarget
from reward.interaction.user_model import build_user_model


def _targets_for_action(a: Action, targets: dict[str, UITarget]) -> dict[str, UITarget]:
    """Return targets with the action's OWN target forced present.

    Some controls are context-conditional (e.g. the composer 'stop' button only
    appears while a turn is running). The ui_map marks those exists=False, but
    when the user performs the action that uses them, they are by definition on
    screen. Forcing just this action's target present avoids a spurious
    'unreachable' penalty while still flagging genuinely missing controls.
    """
    if not a.target_id or a.target_id not in targets:
        return targets
    t = targets[a.target_id]
    if t.exists:
        return targets
    patched = dict(targets)
    patched[a.target_id] = replace(t, exists=True)
    return patched



@dataclass
class EngineResult:
    expected_action_cost_s: float
    mean_task_time_s: float
    stationary: dict[str, float]
    action_costs_s: dict[str, float]
    action_probability: dict[str, float]   # pi[src] * P(action|src)
    task_times_s: dict[str, float]
    meta: dict = field(default_factory=dict)


def _stationary_distribution(graph: ActionGraph, iters: int = 500, tol: float = 1e-9) -> dict[str, float]:
    """Power-iterate the state-transition matrix to its stationary distribution.

    Transition prob from s = sum over out-edges to dst of normalized weight.
    Self-loops (chat->chat actions like send/scroll) keep mass in that state,
    correctly making 'chat' dominant since that is where most actions happen.
    """
    states = list(graph.states)
    idx = {s: i for i, s in enumerate(states)}
    n = len(states)

    # Build row-stochastic matrix P[s][dst].
    P = [[0.0] * n for _ in range(n)]
    for s in states:
        edges = graph.out_edges(s)
        total = sum(max(0.0, a.weight) for a in edges)
        if total <= 0:
            P[idx[s]][idx[s]] = 1.0   # absorbing if no edges
            continue
        for a in edges:
            P[idx[s]][idx[a.dst]] += max(0.0, a.weight) / total

    # Power iteration from uniform.
    pi = [1.0 / n] * n
    for _ in range(iters):
        nxt = [0.0] * n
        for i in range(n):
            pij = P[i]
            pii = pi[i]
            if pii == 0.0:
                continue
            for j in range(n):
                nxt[j] += pii * pij[j]
        # normalize + check convergence
        s = sum(nxt) or 1.0
        nxt = [v / s for v in nxt]
        if max(abs(nxt[k] - pi[k]) for k in range(n)) < tol:
            pi = nxt
            break
        pi = nxt
    return {states[i]: pi[i] for i in range(n)}


def run_engine(
    *,
    days: int = 7,
    log_dir: str = "~/.jcode/logs",
    source_root: str | None = None,
    ops: Operators = Operators(),
) -> EngineResult:
    graph, targets, meta = build_user_model(days=days, log_dir=log_dir, source_root=source_root)

    pi = _stationary_distribution(graph)

    # Price each action. Track previous target per source state so repeat taps
    # in the same state pay a realistic (small) movement cost.
    action_costs: dict[str, float] = {}
    prev_by_state: dict[str, str | None] = {s: None for s in graph.states}
    for a in graph.actions.values():
        eff = _targets_for_action(a, targets)
        bd = action_cost(a, eff, prev_target_id=prev_by_state.get(a.src), ops=ops)
        action_costs[a.id] = bd.seconds
        if a.target_id:
            prev_by_state[a.src] = a.target_id

    # Probability of each action = pi[src] * P(action | src).
    action_prob: dict[str, float] = {}
    for s in graph.states:
        edges = graph.out_edges(s)
        total = sum(max(0.0, e.weight) for e in edges) or 1.0
        for e in edges:
            action_prob[e.id] = pi.get(s, 0.0) * (max(0.0, e.weight) / total)

    # Expected seconds per action (the headline metric).
    psum = sum(action_prob.values()) or 1.0
    expected = sum(action_costs[aid] * p for aid, p in action_prob.items()) / psum

    # Task completion times (sum of action costs in the task's sequence).
    task_times: dict[str, float] = {}
    freq_weighted_num = 0.0
    freq_weighted_den = 0.0
    for t in graph.tasks:
        secs = 0.0
        prev: str | None = None
        for aid in t.action_ids:
            a = graph.actions[aid]
            eff = _targets_for_action(a, targets)
            bd = action_cost(a, eff, prev_target_id=prev, ops=ops)
            secs += bd.seconds
            if a.target_id:
                prev = a.target_id
        task_times[t.id] = round(secs, 3)
        freq_weighted_num += secs * max(0.0, t.frequency)
        freq_weighted_den += max(0.0, t.frequency)
    mean_task = (freq_weighted_num / freq_weighted_den) if freq_weighted_den else 0.0

    return EngineResult(
        expected_action_cost_s=round(expected, 4),
        mean_task_time_s=round(mean_task, 4),
        stationary={k: round(v, 4) for k, v in pi.items()},
        action_costs_s={k: round(v, 4) for k, v in action_costs.items()},
        action_probability={k: round(v, 5) for k, v in action_prob.items()},
        task_times_s=task_times,
        meta=meta,
    )


# Calibration for mapping seconds -> 0..100 reward (higher = cheaper to use).
# Anchors chosen from the model's own range: an expected per-action cost of
# ~1.5s (mostly cheap taps/reads) is excellent (->100); ~6s (deep, slow flows)
# is poor (->0). Linear in between, clamped.
_GOOD_S = 1.5
_BAD_S = 6.0


def reward_score(result: EngineResult | None = None, **kwargs) -> float:
    """Map the engine's expected per-action cost to a 0..100 reward."""
    if result is None:
        result = run_engine(**kwargs)
    s = result.expected_action_cost_s
    if s <= _GOOD_S:
        return 100.0
    if s >= _BAD_S:
        return 0.0
    return round(100.0 * (_BAD_S - s) / (_BAD_S - _GOOD_S), 2)


if __name__ == "__main__":
    import json

    r = run_engine()
    print("usage source:", r.meta.get("usage_source"), "lines:", r.meta.get("lines_scanned"))
    print(f"expected per-action cost: {r.expected_action_cost_s:.3f} s")
    print(f"freq-weighted mean task time: {r.mean_task_time_s:.3f} s")
    print(f"reward score: {reward_score(r):.1f}/100")
    print("stationary distribution:", json.dumps(r.stationary))
    print("task times (s):", json.dumps(r.task_times_s))
    print("action costs (s):")
    for aid, c in sorted(r.action_costs_s.items(), key=lambda kv: -kv[1]):
        print(f"  {aid:16} {c:6.3f}  (p={r.action_probability.get(aid,0):.4f})")
