"""models - the same board, keyed on the model instead of the agent.

The board next door ranks agents: a persona, its tools and its goal, playing a
task under a step budget. Which model was underneath is recorded on every match
and then never asked about again. This file asks about it.

Nothing here runs anything. Every number is read back off matches.jsonl, so a
model appears the moment it has played and disappears from nobody's history
when it stops:

    score      the mean of what the scorers said
    pass       the share of matches where every check passed
    latency    wall clock around the run, and per step, which is the one that
               compares across tasks of different lengths
    tok/s      tokens the run reported over the seconds it took
    spend      cost where the provider charged, tokens where it did not

Rating is the part that has to be careful. Two models that played different
tasks did not meet, and two models that played the same task under different
agents met through a persona that may itself be worth 20 points of score. So
Elo here only moves inside a *controlled* group - the same season, the same
task, the same agent - which is exactly what a gauntlet produces and what the
daily round, playing one model, never does. A model with no such pairing keeps
the starting rating and is flagged `rated: false` rather than being ranked on a
comparison that was never made.
"""
from typing import Any, Dict, Iterable, List, Optional

ELO_START = 1200.0
ELO_K = 32.0
DRAW_MARGIN = 0.02

UNKNOWN = "(unrecorded)"


def name_of(match: Dict[str, Any]) -> str:
    """The model a match ran on.

    Older matches predate the provider recording it, and a run that died before
    it reached the model has none to record — both land here rather than being
    dropped, because a match that happened is a match that happened.
    """
    return str(match.get("model") or UNKNOWN)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _groups(matches: Iterable[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Matches bucketed into controlled comparisons.

    One bucket is one season, one task, one agent — everything except the model
    held still. A bucket with a single model in it compares nothing and is
    dropped; what is left is every head-to-head the log can honestly support.
    """
    buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for m in matches:
        if m.get("void"):
            continue
        key = (m.get("season"), m.get("task"), m.get("agent"))
        buckets.setdefault(key, []).append(m)
    out = []
    for key, rows in buckets.items():
        if len({name_of(m) for m in rows}) < 2:
            continue
        out.append(rows)
    # oldest bucket first: Elo is path-dependent, and replaying the log in the
    # order it was written is the only ordering that means anything
    out.sort(key=lambda rows: min(float(m.get("ts") or 0) for m in rows))
    return out


def _rate(matches: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Replay every controlled group and hand back each model's rating.

    Same maths as the agent board — pairwise, K split across the field, a band
    around equal scores that counts as a draw — so a rating means the same
    thing on both boards.
    """
    ratings: Dict[str, Dict[str, Any]] = {}

    def rec(model: str) -> Dict[str, Any]:
        return ratings.setdefault(model, {
            "elo": ELO_START, "wins": 0, "losses": 0, "draws": 0, "h2h": 0,
            "vs": {},
        })

    for rows in _groups(matches):
        # a model that played the same task twice in one season gets one entry:
        # the mean of what it did, not two votes
        scores: Dict[str, List[float]] = {}
        for m in rows:
            scores.setdefault(name_of(m), []).append(float(m.get("score") or 0.0))
        entries = [(model, _mean(xs)) for model, xs in scores.items()]
        k = ELO_K / (len(entries) - 1)
        deltas = {model: 0.0 for model, _ in entries}
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
        for model, d in deltas.items():
            rec(model)["elo"] = round(rec(model)["elo"] + d, 1)
    return ratings


def _tally(r: Dict[str, Any], other: str, outcome: float) -> None:
    r["h2h"] += 1
    vs = r["vs"].setdefault(other, {"wins": 0, "losses": 0, "draws": 0})
    if outcome > 0.5:
        r["wins"] += 1
        vs["wins"] += 1
    elif outcome < 0.5:
        r["losses"] += 1
        vs["losses"] += 1
    else:
        r["draws"] += 1
        vs["draws"] += 1


def _row(model: str, rows: List[Dict[str, Any]],
         rating: Dict[str, Any] = None) -> Dict[str, Any]:
    """One model's line: what it scored, how fast, and what it burned."""
    played = [m for m in rows if not m.get("void")]
    voids = len(rows) - len(played)
    scores = [float(m.get("score") or 0.0) for m in played]
    seconds = [float(m.get("seconds") or 0.0) for m in played]
    steps = sum(int(m.get("steps") or 0) for m in played)
    secs = sum(seconds)
    tokens = sum(int(m.get("tokens") or 0) for m in played)
    cost = sum(float(m.get("cost") or 0.0) for m in played)
    n = max(1, len(played))
    rating = rating or {}
    return {
        "model": model,
        # the provider is only on matches recorded since the gauntlet shipped —
        # an older row says nothing rather than guessing from the model id
        "provider": next((m.get("provider") for m in rows if m.get("provider")), None),
        # a model nobody was charged for. Read off the meter, not off the id:
        # ':free' in a name is a convention, a $0 bill is a fact
        "free": cost <= 0,
        "elo": round(float(rating.get("elo", ELO_START)), 1),
        # false = it never met another model on equal terms, so its Elo is the
        # number it started with and the board says so instead of implying rank
        "rated": bool(rating.get("h2h")),
        "h2h": int(rating.get("h2h", 0)),
        "wins": int(rating.get("wins", 0)),
        "losses": int(rating.get("losses", 0)),
        "draws": int(rating.get("draws", 0)),
        "matches": len(played),
        "voids": voids,
        "avg_score": round(_mean(scores), 4),
        "best_score": round(max(scores), 4) if scores else 0.0,
        "pass_rate": round(sum(1 for m in played if m.get("passed")) / n, 3),
        "avg_seconds": round(_mean(seconds), 2),
        "p50_seconds": round(_median(seconds), 2),
        "max_seconds": round(max(seconds), 2) if seconds else 0.0,
        # the latency that survives being compared across tasks: a 3-step task
        # and a 12-step one are not the same run, but a step is a step
        "sec_per_step": round(secs / steps, 2) if steps else 0.0,
        "tok_per_sec": round(tokens / secs, 1) if secs else 0.0,
        "steps": steps,
        "tokens": tokens,
        "avg_tokens": int(tokens / n),
        "cost": round(cost, 6),
        "cost_per_match": round(cost / n, 6),
        # what a point of score costs on this model — the whole argument for
        # running the cheap one, in one number
        "cost_per_point": round(cost / sum(scores), 6) if cost and sum(scores) else 0.0,
        "tasks": sorted({str(m.get("task")) for m in played}),
        "suites": sorted({str(m.get("suite")) for m in played if m.get("suite")}),
        "agents": sorted({str(m.get("agent")) for m in played if m.get("agent")}),
        "first": min((float(m.get("ts") or 0) for m in rows), default=0),
        "last": max((float(m.get("ts") or 0) for m in rows), default=0),
    }


def board(matches: Iterable[Dict[str, Any]],
          min_matches: int = 1) -> List[Dict[str, Any]]:
    """Every model that has played, ranked.

    Elo first, because that is the only number here built out of comparisons;
    within the models that never met — all sitting on the starting rating —
    the tiebreak is what they actually scored.
    """
    matches = list(matches)
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for m in matches:
        by_model.setdefault(name_of(m), []).append(m)
    ratings = _rate(matches)
    rows = [_row(model, rows, ratings.get(model))
            for model, rows in by_model.items()
            if len(rows) >= max(1, int(min_matches))]
    rows.sort(key=lambda r: (-r["elo"], -r["avg_score"], r["model"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def card(matches: Iterable[Dict[str, Any]], model: str,
         titles: Dict[str, str] = None) -> Dict[str, Any]:
    """One model's record: its line, its per-task scores, who it beat."""
    matches = list(matches)
    mine = [m for m in matches if name_of(m) == model]
    ratings = _rate(matches)
    rating = ratings.get(model, {})
    titles = titles or {}

    per_task: Dict[str, Dict[str, Any]] = {}
    for m in sorted(mine, key=lambda x: float(x.get("ts") or 0)):
        if m.get("void"):
            continue
        key = str(m.get("task"))
        t = per_task.setdefault(key, {
            "task": key, "title": m.get("title") or titles.get(key, key),
            "suite": m.get("suite"), "n": 0, "best": 0.0, "last": 0.0,
            "seconds": 0.0, "tokens": 0,
        })
        score = float(m.get("score") or 0.0)
        t["n"] += 1
        t["last"] = score
        t["best"] = max(t["best"], score)
        t["seconds"] = round(float(m.get("seconds") or 0.0), 2)
        t["tokens"] = int(m.get("tokens") or 0)

    opponents = [
        {"model": other, **rec,
         "record": f"{rec['wins']}-{rec['losses']}-{rec['draws']}"}
        for other, rec in sorted((rating.get("vs") or {}).items(),
                                 key=lambda kv: -(kv[1]["wins"] + kv[1]["losses"]
                                                  + kv[1]["draws"]))
    ]
    row = next((r for r in board(matches) if r["model"] == model),
               _row(model, mine, rating))
    return {
        **row,
        "per_task": sorted(per_task.values(), key=lambda t: -t["last"]),
        "opponents": opponents,
        "matches_log": sorted(mine, key=lambda m: -float(m.get("ts") or 0))[:20],
    }


def task_board(matches: Iterable[Dict[str, Any]],
               titles: Dict[str, str] = None) -> List[Dict[str, Any]]:
    """Every task that has been played, hardest first, with the models that
    played it ranked underneath.

    This is the other way round the board can be read: not "which model is
    best" but "which model is best at *this*" — and, since the mean score is
    right there, which tasks are actually discriminating between models and
    which ones everybody either aces or fails.
    """
    titles = titles or {}
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for m in matches:
        by_task.setdefault(str(m.get("task")), []).append(m)

    out = []
    for key, rows in by_task.items():
        played = [m for m in rows if not m.get("void")]
        scores = [float(m.get("score") or 0.0) for m in played]
        by_model: Dict[str, List[Dict[str, Any]]] = {}
        for m in played:
            by_model.setdefault(name_of(m), []).append(m)
        models = [{
            "model": model,
            "n": len(ms),
            "score": round(_mean([float(x.get("score") or 0.0) for x in ms]), 4),
            "pass_rate": round(sum(1 for x in ms if x.get("passed")) / len(ms), 3),
            "seconds": round(_mean([float(x.get("seconds") or 0.0) for x in ms]), 2),
            "tokens": int(_mean([float(x.get("tokens") or 0) for x in ms])),
            "agents": sorted({str(x.get("agent")) for x in ms}),
        } for model, ms in by_model.items()]
        models.sort(key=lambda r: (-r["score"], r["seconds"]))
        latest = max(rows, key=lambda m: float(m.get("ts") or 0))
        out.append({
            "task": key,
            "title": latest.get("title") or titles.get(key, key),
            "suite": latest.get("suite"),
            "matches": len(played),
            "voids": len(rows) - len(played),
            "avg_score": round(_mean(scores), 4),
            "pass_rate": round(sum(1 for m in played if m.get("passed"))
                               / max(1, len(played)), 3),
            "avg_seconds": round(_mean([float(m.get("seconds") or 0.0)
                                        for m in played]), 2),
            # the spread between the best and worst model on this task: a task
            # everyone scores the same on ranks nobody
            "spread": round(max([m["score"] for m in models], default=0.0)
                            - min([m["score"] for m in models], default=0.0), 4),
            "models": models,
            "best": models[0]["model"] if models else None,
            "last": float(latest.get("ts") or 0),
        })
    out.sort(key=lambda r: (r["avg_score"], -r["matches"]))
    return out
