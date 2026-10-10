"""tiers - the same matches, read as "does this agent design survive a cheap model".

models.py holds the agent still and moves the model: that board answers "which
model is better". This file does the opposite. It holds the MODEL still and
moves the agent, because the question a framework is actually judged on is not
which model wins — it is how much of the work the *design* is doing:

    a prompt, a toolbox and a step budget that only score on a frontier model
    were never the thing scoring. The model was.

So there are three readings here, all off matches.jsonl, none of them running
anything:

    field(model)   agents ranked inside ONE model — the leaderboard of designs
                   at that price point
    board()        one row per model that has played two or more agents: the
                   SPREAD between its best and worst agent, which is the whole
                   point — a tier where every design scores the same is a tier
                   that ranks nobody, and a wide spread is design mattering
    matrix()       agents x models, with retention: what share of an agent's
                   score on the reference model it keeps on the cheap one,
                   computed only over tasks both of them actually played

Rating inside a tier is the same controlled-group Elo the model board uses,
with the axis swapped: one bucket is one season, one task, one MODEL, and the
agent is the only thing that moves. An agent that never met another inside a
tier is `rated: false` rather than ranked on a comparison nobody made.

Honesty rule that runs through all of it: two cells are only compared over the
tasks they share. An agent that played three tasks on haiku and eight on opus
has not lost 60% of its score, and a board that said so would be worse than no
board.
"""
from typing import Any, Dict, Iterable, List, Optional

from .models import (ELO_START, ELO_K, DRAW_MARGIN, UNKNOWN,
                     _mean, _median, _tally, name_of)


def agent_of(match: Dict[str, Any]) -> str:
    return str(match.get("agent") or UNKNOWN)


def _played(matches: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in matches if not m.get("void")]


# ── rating inside one tier ───────────────────────────────────────────

def _groups(matches: Iterable[Dict[str, Any]], model: str = None) -> List[List[Dict[str, Any]]]:
    """Matches bucketed into comparisons where the agent is the only variable.

    One bucket is one season, one task, one model. A bucket holding a single
    agent compares nothing and is dropped.
    """
    buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for m in _played(matches):
        if model is not None and name_of(m) != model:
            continue
        buckets.setdefault((m.get("season"), m.get("task"), name_of(m)), []).append(m)
    out = [rows for rows in buckets.values()
           if len({agent_of(m) for m in rows}) >= 2]
    # oldest first: Elo is path-dependent, so the log is replayed in the order
    # it was written
    out.sort(key=lambda rows: min(float(m.get("ts") or 0) for m in rows))
    return out


def _rate(matches: Iterable[Dict[str, Any]], model: str = None) -> Dict[str, Dict[str, Any]]:
    """Replay every controlled group and hand back each agent's tier rating."""
    ratings: Dict[str, Dict[str, Any]] = {}

    def rec(agent: str) -> Dict[str, Any]:
        return ratings.setdefault(agent, {
            "elo": ELO_START, "wins": 0, "losses": 0, "draws": 0, "h2h": 0, "vs": {},
        })

    for rows in _groups(matches, model):
        scores: Dict[str, List[float]] = {}
        for m in rows:
            scores.setdefault(agent_of(m), []).append(float(m.get("score") or 0.0))
        entries = [(a, _mean(xs)) for a, xs in scores.items()]
        k = ELO_K / (len(entries) - 1)
        deltas = {a: 0.0 for a, _ in entries}
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, sa = entries[i]
                b, sb = entries[j]
                ra, rb = rec(a)["elo"], rec(b)["elo"]
                expected = 1 / (1 + 10 ** ((rb - ra) / 400))
                if sa > sb + DRAW_MARGIN:
                    outcome = 1.0
                elif sb > sa + DRAW_MARGIN:
                    outcome = 0.0
                else:
                    outcome = 0.5
                d = k * (outcome - expected)
                deltas[a] += d
                deltas[b] -= d
                _tally(rec(a), b, outcome)
                _tally(rec(b), a, 1 - outcome)
        for a, d in deltas.items():
            rec(a)["elo"] = round(rec(a)["elo"] + d, 1)
    return ratings


# ── one tier: the agents inside it ───────────────────────────────────

def _agent_row(agent: str, rows: List[Dict[str, Any]],
               rating: Dict[str, Any] = None,
               common: Optional[set] = None) -> Dict[str, Any]:
    """One agent's line inside a tier."""
    played = _played(rows)
    voids = len(rows) - len(played)
    scores = [float(m.get("score") or 0.0) for m in played]
    seconds = [float(m.get("seconds") or 0.0) for m in played]
    steps = sum(int(m.get("steps") or 0) for m in played)
    tokens = sum(int(m.get("tokens") or 0) for m in played)
    cost = sum(float(m.get("cost") or 0.0) for m in played)
    n = max(1, len(played))
    rating = rating or {}
    # the number the tier is ranked on is the one every agent was measured on:
    # the tasks the whole field played here, not whatever each agent happened
    # to draw
    shared = [float(m.get("score") or 0.0) for m in played
              if common is None or str(m.get("task")) in common]
    return {
        "agent": agent,
        "elo": round(float(rating.get("elo", ELO_START)), 1),
        "rated": bool(rating.get("h2h")),
        "h2h": int(rating.get("h2h", 0)),
        "wins": int(rating.get("wins", 0)),
        "losses": int(rating.get("losses", 0)),
        "draws": int(rating.get("draws", 0)),
        "matches": len(played),
        "voids": voids,
        # score over the shared task set — what the ranking uses
        "score": round(_mean(shared), 4),
        # ...and over everything it played here, which is what its own card says
        "avg_score": round(_mean(scores), 4),
        "best_score": round(max(scores), 4) if scores else 0.0,
        "pass_rate": round(sum(1 for m in played if m.get("passed")) / n, 3),
        "avg_seconds": round(_mean(seconds), 2),
        "p50_seconds": round(_median(seconds), 2),
        "sec_per_step": round(sum(seconds) / steps, 2) if steps else 0.0,
        "steps": steps,
        "tokens": tokens,
        "avg_tokens": int(tokens / n),
        "cost": round(cost, 6),
        "cost_per_point": round(cost / sum(scores), 6) if cost and sum(scores) else 0.0,
        "tasks": sorted({str(m.get("task")) for m in played}),
        "last": max((float(m.get("ts") or 0) for m in rows), default=0),
    }


def field(matches: Iterable[Dict[str, Any]], model: str,
          titles: Dict[str, str] = None) -> Dict[str, Any]:
    """One tier in full: every agent that played this model, ranked.

    This is the board a framework is read off — same model, same tasks, same
    budget, the design the only thing that differs.
    """
    matches = list(matches)
    mine = [m for m in matches if name_of(m) == model]
    by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for m in mine:
        by_agent.setdefault(agent_of(m), []).append(m)
    common = _common_tasks(by_agent)
    ratings = _rate(matches, model)
    rows = [_agent_row(a, rs, ratings.get(a), common)
            for a, rs in by_agent.items()]
    rows.sort(key=lambda r: (-r["elo"], -r["score"], r["agent"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    spread = _spread(rows)
    # a gap between averages taken on different tasks is not a claim about
    # design, so the tier does not get to say it separates anybody
    spread["separates"] = spread["separates"] and common is not None
    return {
        "model": model,
        "provider": next((m.get("provider") for m in mine if m.get("provider")), None),
        "free": sum(float(m.get("cost") or 0.0) for m in mine) <= 0,
        "agents": rows,
        # the tasks every ranked agent played here — the ones the comparison
        # actually rests on. Empty means they never all played the same one,
        # and the ranking below is each agent's own average instead
        "tasks": sorted(common or []),
        "comparable": common is not None,
        "task_titles": {k: (titles or {}).get(k, k) for k in sorted(common or [])},
        "matches": len(_played(mine)),
        "voids": len(mine) - len(_played(mine)),
        **spread,
        "per_task": _per_task(mine, titles),
    }


def _common_tasks(by_agent: Dict[str, List[Dict[str, Any]]]) -> Optional[set]:
    """The tasks every agent in the group played, or None when there are none.

    None is the honest answer to "what did they all play", not a filter that
    matches nothing — a field whose agents drifted onto different rotations
    over several seasons has an empty intersection, and scoring everyone over
    it would hand the whole tier a flat zero. The caller falls back to each
    agent's own average and flags the tier `comparable: false`, which says the
    same thing without inventing a number.
    """
    sets = [{str(m.get("task")) for m in _played(rows)}
            for rows in by_agent.values() if _played(rows)]
    if not sets:
        return None
    common = set.intersection(*sets)
    return common or None


def _spread(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Best minus worst design inside a tier — how much the design is worth
    here. Near zero and the tier separates nobody: every agent is being carried
    by (or drowned by) the model."""
    ranked = [r for r in rows if r["matches"]]
    if len(ranked) < 2:
        return {"spread": 0.0, "best": ranked[0]["agent"] if ranked else None,
                "worst": None, "avg_score": round(_mean([r["score"] for r in ranked]), 4)
                if ranked else 0.0, "separates": False}
    scores = [r["score"] for r in ranked]
    best = max(ranked, key=lambda r: r["score"])
    worst = min(ranked, key=lambda r: r["score"])
    spread = round(best["score"] - worst["score"], 4)
    return {
        "spread": spread,
        "best": best["agent"],
        "worst": worst["agent"],
        "avg_score": round(_mean(scores), 4),
        # a tier where the whole field lands within a rounding error of each
        # other is not measuring design, whatever its Elo column says
        "separates": spread >= 0.05,
    }


def _per_task(rows: List[Dict[str, Any]], titles: Dict[str, str] = None) -> List[Dict[str, Any]]:
    """Inside one tier, per task: who leads it and how far apart the field is."""
    titles = titles or {}
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for m in _played(rows):
        by_task.setdefault(str(m.get("task")), []).append(m)
    out = []
    for key, ms in by_task.items():
        by_agent: Dict[str, List[float]] = {}
        for m in ms:
            by_agent.setdefault(agent_of(m), []).append(float(m.get("score") or 0.0))
        entries = sorted(((a, round(_mean(xs), 4)) for a, xs in by_agent.items()),
                         key=lambda kv: -kv[1])
        latest = max(ms, key=lambda m: float(m.get("ts") or 0))
        out.append({
            "task": key,
            "title": latest.get("title") or titles.get(key, key),
            "suite": latest.get("suite"),
            "n": len(ms),
            "avg_score": round(_mean([s for _, s in entries]), 4),
            "spread": round(entries[0][1] - entries[-1][1], 4) if len(entries) > 1 else 0.0,
            "best": entries[0][0] if entries else None,
            "agents": [{"agent": a, "score": s} for a, s in entries],
        })
    out.sort(key=lambda t: (-t["spread"], t["avg_score"]))
    return out


# ── every tier, as one board ─────────────────────────────────────────

def board(matches: Iterable[Dict[str, Any]], min_agents: int = 2) -> List[Dict[str, Any]]:
    """One row per model the field has played, cheapest-first thinking:

    how many designs have met on it, what they averaged, and the spread between
    the best and the worst. A tier is only worth running a framework on if that
    spread is real — otherwise the board is measuring the model.
    """
    matches = list(matches)
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for m in matches:
        by_model.setdefault(name_of(m), []).append(m)
    out = []
    for model, rows in by_model.items():
        by_agent: Dict[str, List[Dict[str, Any]]] = {}
        for m in rows:
            by_agent.setdefault(agent_of(m), []).append(m)
        if len(by_agent) < max(1, int(min_agents)):
            continue
        common = _common_tasks(by_agent)
        ratings = _rate(matches, model)
        agents = [_agent_row(a, rs, ratings.get(a), common) for a, rs in by_agent.items()]
        agents.sort(key=lambda r: (-r["elo"], -r["score"]))
        played = _played(rows)
        cost = sum(float(m.get("cost") or 0.0) for m in played)
        scores = [float(m.get("score") or 0.0) for m in played]
        spread = _spread(agents)
        spread["separates"] = spread["separates"] and common is not None
        out.append({
            "model": model,
            "provider": next((m.get("provider") for m in rows if m.get("provider")), None),
            "free": cost <= 0,
            "agents_n": len(by_agent),
            "agents": [a["agent"] for a in agents],
            "matches": len(played),
            "voids": len(rows) - len(played),
            "tasks": sorted(common or []),
            "tasks_n": len(common or []),
            # false = no task the whole field played here, so the spread is
            # over averages that were taken on different work
            "comparable": common is not None,
            "pass_rate": round(sum(1 for m in played if m.get("passed"))
                               / max(1, len(played)), 3),
            "avg_seconds": round(_mean([float(m.get("seconds") or 0.0) for m in played]), 2),
            "tokens": sum(int(m.get("tokens") or 0) for m in played),
            "cost": round(cost, 6),
            "cost_per_match": round(cost / max(1, len(played)), 6),
            "leader": agents[0] if agents else None,
            "last": max((float(m.get("ts") or 0) for m in rows), default=0),
            **spread,
        })
    # the tier that separates designs most, first — that is the one worth
    # running a framework against
    out.sort(key=lambda r: (-r["spread"], -r["agents_n"], r["model"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


# ── across tiers: what survives the downgrade ────────────────────────

def _cells(matches: Iterable[Dict[str, Any]]) -> Dict[tuple, Dict[str, List[float]]]:
    """{(agent, model): {task: [scores]}} — the grid everything below reads."""
    cells: Dict[tuple, Dict[str, List[float]]] = {}
    for m in _played(matches):
        cell = cells.setdefault((agent_of(m), name_of(m)), {})
        cell.setdefault(str(m.get("task")), []).append(float(m.get("score") or 0.0))
    return cells


def _paired(cell: Dict[str, List[float]], other: Dict[str, List[float]]) -> tuple:
    """Two cells scored over the tasks they share, or (None, None, 0)."""
    shared = sorted(set(cell) & set(other))
    if not shared:
        return None, None, 0
    return (round(_mean([_mean(cell[t]) for t in shared]), 4),
            round(_mean([_mean(other[t]) for t in shared]), 4),
            len(shared))


def matrix(matches: Iterable[Dict[str, Any]], ref: str = None,
           agents: List[str] = None, models: List[str] = None) -> Dict[str, Any]:
    """Agents down the side, models across the top, retention in the cells.

    `retention` is the share of an agent's reference-model score it keeps on
    this one, over the tasks both cells played. It is the number that says
    whether a design is portable: two agents can tie on the frontier model and
    be 30 points apart the moment the model gets small, and that gap is the
    design.

    The reference is the model with the highest mean score unless one is named.
    """
    matches = list(matches)
    cells = _cells(matches)
    all_agents = agents or sorted({a for a, _ in cells})
    all_models = models or sorted({m for _, m in cells})

    if not ref:
        means = {m: _mean([_mean(sc) for (a, mm), c in cells.items() if mm == m
                           for sc in c.values()]) for m in all_models}
        ref = max(means, key=lambda m: means[m]) if means else None

    rows = []
    for a in all_agents:
        base = cells.get((a, ref)) or {}
        cols = []
        for m in all_models:
            cell = cells.get((a, m))
            if not cell:
                cols.append({"model": m, "n": 0, "score": None, "retention": None})
                continue
            n = sum(len(v) for v in cell.values())
            score = round(_mean([_mean(v) for v in cell.values()]), 4)
            here, there, shared = _paired(cell, base) if base else (None, None, 0)
            cols.append({
                "model": m,
                "n": n,
                "tasks": len(cell),
                "score": score,
                # only over shared tasks, and only against a different model
                "vs_ref": here if m != ref and shared else None,
                "ref_score": there if m != ref and shared else None,
                "shared": shared,
                "retention": (round(here / there, 3) if shared and there else
                              (1.0 if m == ref and cell else None)),
            })
        scored = [c for c in cols if c["retention"] is not None and c["model"] != ref]
        rows.append({
            "agent": a,
            "cells": cols,
            "ref_score": round(_mean([_mean(v) for v in base.values()]), 4) if base else None,
            # one number per design: how well it travels down the price list
            "retention": round(_mean([c["retention"] for c in scored]), 3) if scored else None,
            "tiers": sum(1 for c in cols if c["n"]),
        })
    rows.sort(key=lambda r: (-(r["retention"] if r["retention"] is not None else -1),
                             -(r["ref_score"] or 0), r["agent"]))
    return {
        "ref": ref,
        "models": all_models,
        "agents": all_agents,
        "rows": rows,
        # the designs whose score holds up when the model gets cheap, and the
        # ones that were only ever the frontier model talking
        "portable": [r["agent"] for r in rows
                     if r["retention"] is not None and r["retention"] >= 0.9],
        "carried": [r["agent"] for r in rows
                    if r["retention"] is not None and r["retention"] < 0.6],
    }
