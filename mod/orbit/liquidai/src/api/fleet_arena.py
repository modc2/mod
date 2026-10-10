"""fleet_arena — the door from this module to the arena module's games.

liquidai and the arena already know each other in one direction: every
`model` seat in the arena is an LFM, answered by this module's /v1 (the
arena's players.rs defaults `base` to this box's liquidai). This file is the
other direction — browse the arena's games, seat an LFM at one, run a match,
read the Elo board — all from liquidai's own surface. Nothing here referees
a game: the arena stays the only scorer, this is a client of it.

The arena is a sibling module on this box (:50470), found by ARENA_API or
the default port. Seating is idempotent on the arena side — entering a name
that exists updates it and keeps its record — so a model's seat is stable
across matches and its Elo history is one line, not a pile of duplicates.
"""

import os
import re
from typing import Any, Dict, List, Optional

import requests

BASE = os.environ.get("ARENA_API", "http://127.0.0.1:50470").rstrip("/")
TIMEOUT = 20
SEAT_NOTE = "an LFM seated by the liquidai module"


class ArenaDown(ValueError):
    """The arena module isn't answering on this box."""


def _err(resp: requests.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error") or body.get("detail") or body)[:300]
    except Exception:
        return resp.text[:300]


def _get(path: str, **params: Any) -> Any:
    try:
        r = requests.get(f"{BASE}{path}", params={k: v for k, v in params.items()
                                                  if v is not None}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ArenaDown(f"arena module unreachable at {BASE} — `m arena/serve` ({e})")
    if not r.ok:
        raise ValueError(f"arena {path}: {_err(r)}")
    return r.json()


def _post(path: str, body: Dict[str, Any], timeout: float = TIMEOUT) -> Any:
    try:
        r = requests.post(f"{BASE}{path}", json=body, timeout=timeout)
    except requests.RequestException as e:
        raise ArenaDown(f"arena module unreachable at {BASE} — `m arena/serve` ({e})")
    if not r.ok:
        raise ValueError(f"arena {path}: {_err(r)}")
    return r.json()


def status() -> Dict[str, Any]:
    """Is the arena up, and how big is it."""
    try:
        health = _get("/health")
    except ArenaDown as e:
        return {"ok": False, "base": BASE, "error": str(e)}
    return {"ok": True, "base": BASE, "version": health.get("version"),
            "games": health.get("games"), "players": health.get("players"),
            "matches": health.get("matches")}


def games(q: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every game stored in the arena, trimmed to what a match needs."""
    out = _get("/modules", limit=500)
    rows = out if isinstance(out, list) else out.get("modules", [])
    games_ = [m for m in rows if m.get("role") == "game"]
    if q:
        needle = q.lower()
        games_ = [g for g in games_
                  if needle in g.get("name", "").lower()
                  or needle in g.get("description", "").lower()]
    games_.sort(key=lambda g: (-(g.get("runs") or 0), g.get("name", "")))
    return [{"id": g["id"], "short": g.get("short", g["id"][:12]),
             "name": g.get("name", ""), "description": g.get("description", ""),
             "lang": g.get("lang", ""), "runs": g.get("runs", 0),
             "origin": g.get("origin", ""), "tags": g.get("tags", [])}
            for g in games_]


def seat_name(model: str) -> str:
    """The player name an LFM sits under — the bare model, org stripped.

    The arena board reads better as `LFM2.5-350M` than as a repo path, and
    slashes in a name make awkward URLs on their side.
    """
    return re.sub(r"^LiquidAI/", "", model.strip()).strip("/") or model.strip()


def seat(model: str, system: Optional[str] = None,
         temperature: Optional[float] = None, max_tokens: Optional[int] = None,
         owner: str = "") -> Dict[str, Any]:
    """Enter (or update) an LFM as a `model` player in the arena.

    The arena's model driver already defaults its base to this module's /v1,
    so config carries only the model and the knobs — no URL, no key.
    """
    config: Dict[str, Any] = {"model": model.strip()}
    if system:
        config["system"] = system
    if temperature is not None:
        config["temperature"] = temperature
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    return _post("/players", {"name": seat_name(model), "kind": "model",
                              "config": config, "owner": owner, "note": SEAT_NOTE})


def match(game: str, models: List[str], system: Optional[str] = None,
          seed: Optional[int] = None, turns: Optional[int] = None,
          timeout_ms: Optional[int] = None, owner: str = "",
          opponents: Optional[List[str]] = None) -> Dict[str, Any]:
    """Seat each model, then have the arena run them through `game`.

    The match executes entirely in the arena's runtime; every move it asks a
    seat for comes back through this module's /v1. One entrant is practice,
    two or more is rated — the arena's rule, not ours. `opponents` are
    players already seated over there — a wasm minimax, another module's
    agent — taken as-is by name, so an LFM can be measured against something
    that isn't an LFM.
    """
    models = [m.strip() for m in models if m and m.strip()]
    opponents = [o.strip() for o in (opponents or []) if o and o.strip()]
    if not models:
        raise ValueError("no models to seat")
    if len(models) + len(opponents) > 8:
        raise ValueError("eight seats is the most any arena game takes")
    seats = [seat(m, system=system, owner=owner) for m in models]
    names = [s.get("player", {}).get("name") or s.get("name") or seat_name(m)
             for s, m in zip(seats, models)]
    body: Dict[str, Any] = {"game": game, "players": names + opponents}
    if seed is not None:
        body["seed"] = int(seed)
    if turns is not None:
        body["turns"] = int(turns)
    if timeout_ms is not None:
        body["timeout_ms"] = int(timeout_ms)
    # a match of small models on CPU is minutes, not seconds — wait for it
    wait = max(TIMEOUT, ((timeout_ms or 300_000) / 1000) + 30)
    out = _post("/run", body, timeout=wait)
    return {"seated": names, **(out if isinstance(out, dict) else {"result": out})}


def board(game: Optional[str] = None, limit: int = 50,
          lfm_only: bool = False) -> Dict[str, Any]:
    """The arena's Elo board — optionally only the seats this module put there."""
    out = _get("/leaderboard", game=game, limit=limit)
    players = out.get("players") or []
    if lfm_only:
        players = [p for p in players if p.get("kind") == "model"]
    return {"game": game, "count": len(players), "players": players}


def matches(game: Optional[str] = None, player: Optional[str] = None,
            limit: int = 20) -> Dict[str, Any]:
    """Recent arena matches, newest first."""
    return _get("/matches", game=game, player=player, limit=limit)


def match_detail(match_id: str) -> Dict[str, Any]:
    """One match in full — every turn, every prompt, every read move."""
    return _get(f"/matches/{match_id}")
