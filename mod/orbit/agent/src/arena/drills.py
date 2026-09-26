"""drills - the agent board plays the arena module's drill games

`orbit/arena` is the other arena in this fleet: you upload a Python class or a
wasm module and it *is* a game, and players — bots, models, agents — are seated
at it and rated. Most of what lives there is a game in the ordinary sense, two
seats taking turns, which does not fit a board that runs an agent once and
scores what it left behind.

Its *drills* do fit, exactly. A drill is a game whose questions are generated
from the seed and graded on the spot: `addup`, `times`, `jsonpath`, `jsonfix`,
`invoice` — adding, multiplying, reading a JSON document, repairing a broken
one, and an invoice that needs all four. One round is one question with one
right answer, which is precisely the shape of a task here:

    key       arena#invoice@4
    prompt    the view that drill shows a seat at that round — the document,
              the question, and what a good answer looks like
    scorers   [{"type": "arena", "game": "invoice", "seed": 7, "round": 4,
                "path": "answer.txt"}]

At scoring time the `arena` scorer takes what the agent wrote and plays it into
a table at that game, over the game's own MCP server, and reads back whether it
stood. **The arena grades it, in its own sandbox, running its own class.** This
module never evaluates a drill's answer and never holds a copy of one, so an
agent rated here on `invoice` was rated by the same code that rates `calc` and
every model sitting over there.

Two properties of the neighbour make this cheap, and both are its own promises
rather than assumptions made here:

  * a table is `(seed, moves)` and is replayed from scratch on every call, so
    "round 4 of seed 7" is a thing that can be asked for at any time, from any
    process, with no session to keep alive
  * a drill's questions are a function of the seed alone, never of the answers,
    so a table can be walked forward to round 4 with blank moves and the
    question there is the same one a seat that answered every round would face

    from src.arena import drills
    drills.available()                     # is the arena next door up?
    drills.pool(rounds=2)                  # its drills, as agent-arena tasks
    drills.sheet("addup", seed=7, round=0) # the view, as a seat sees it
    drills.grade("addup", 7, 0, "109")     # played into a table over there

Everything is best-effort: the arena is an optional neighbour, so a module that
is down means "no drill tasks in the pool", never a traceback in a round.
"""
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # the neighbour is optional, and so is its client
    requests = None


# where the arena answers, unless something says otherwise
DEFAULT_BASE = "http://127.0.0.1:50470"

SUITE = "arena"

# the tag a drill carries in the arena's registry. A game without it is a game
# in the ordinary sense — two seats, many turns — and does not belong here
DRILL_TAG = "drill"

# One question is one answer: no file to compile, no test to run. Four steps is
# room to read, think and write, and an agent that wants more than that on a
# sum is telling us something.
DEFAULT_STEPS = 4

# the seed every drill task is asked at. Fixed on purpose: two agents are only
# comparable on a drill if they were asked the same questions, and a task key
# that means a different question each week is not a task.
DEFAULT_SEED = 7

# how many rounds of each drill join the pool, spread across the difficulty
# ramp rather than taken from the front
DEFAULT_ROUNDS = 2

LIST_TTL = 30.0
T_LIST = 5.0
# a table replays from its seed on every call, and each replay is a python
# sandbox starting up — several of those, in a row, on one grade
T_PLAY = 90.0

_lock = threading.Lock()
_games: Dict[str, Any] = {"ts": 0.0, "rows": []}
_sheets: Dict[str, str] = {}


class Unavailable(RuntimeError):
    """The arena could not be reached. A judge that is down is not a failed
    agent, so callers turn this into a void match rather than a lost one."""


# ── where it lives ───────────────────────────────────────────────────

def base() -> str:
    """The arena's API root: the environment, then its config, then the default."""
    env = (os.environ.get("ARENA_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        # ../arena/config.json — the fleet keeps modules side by side
        cfg = Path(__file__).resolve().parents[3] / "arena" / "config.json"
        data = json.loads(cfg.read_text())
        url = ((data.get("urls") or {}).get("api") or "").strip().rstrip("/")
        if url:
            return url
        port = int(data.get("port") or 0)
        if port:
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass
    return DEFAULT_BASE


def _get(path: str, timeout: float = T_LIST) -> Any:
    if requests is None:
        raise Unavailable("python `requests` is not installed")
    try:
        r = requests.get(f"{base()}{path}", timeout=timeout)
    except Exception as e:
        raise Unavailable(f"the arena is unreachable at {base()}: {e}")
    if r.status_code >= 400:
        raise Unavailable(f"arena {path} -> {r.status_code} {r.text[:160]}")
    return r.json()


def _rpc(game: str, tool: str, args: Dict[str, Any], timeout: float = T_PLAY) -> Dict[str, Any]:
    """Call one tool on a game's own MCP server — `/m/<game>/mcp`.

    Every game in that registry has one, and it is the turn-taking door: `open`
    a table, read the `view`, send a `move`. We speak it directly rather than
    through a client library because it is four lines of JSON-RPC and a
    dependency is a thing that can be missing.
    """
    if requests is None:
        raise Unavailable("python `requests` is not installed")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    try:
        r = requests.post(f"{base()}/m/{game}/mcp", json=body, timeout=timeout)
    except Exception as e:
        raise Unavailable(f"the arena is unreachable at {base()}: {e}")
    try:
        out = r.json()
    except Exception:
        raise Unavailable(f"arena /m/{game}/mcp -> {r.status_code} {r.text[:160]}")

    result = out.get("result") or {}
    if out.get("error"):
        raise Unavailable(f"arena {tool}: {str(out['error'])[:200]}")
    if result.get("isError"):
        # A tool error is the arena saying no to *this call* — an unknown game,
        # a table that aged out, a move at a finished game. That is a real
        # answer, not an outage, but nothing here can proceed on it.
        text = ""
        for block in result.get("content") or []:
            text += str(block.get("text") or "")
        raise Unavailable(f"arena {tool}: {text[:200] or 'refused'}")

    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content") or []:
        try:
            return json.loads(block.get("text") or "")
        except ValueError:
            continue
    raise Unavailable(f"arena {tool} returned nothing readable")


def info() -> Dict[str, Any]:
    """The arena's own info document — raises Unavailable if it is not up."""
    return _get("/health")


def available() -> bool:
    try:
        info()
        return True
    except Exception:
        return False


def status() -> Dict[str, Any]:
    """A card for the console: is it up, and what can be played."""
    try:
        rows = games(ttl=0.0)
    except Unavailable as e:
        return {"available": False, "base": base(), "error": str(e), "games": []}
    return {
        "available": True,
        "base": base(),
        "games": [{"name": g["name"], "description": g.get("description", ""),
                   "rounds": rounds_of(g)} for g in rows],
    }


# ── the drills over there ────────────────────────────────────────────

def games(ttl: float = LIST_TTL) -> List[Dict[str, Any]]:
    """Every drill game in the arena's registry. Cached briefly — the console
    polls, and anyone can upload a new one at any moment."""
    import time
    with _lock:
        if ttl and _games["rows"] and (time.time() - _games["ts"]) < ttl:
            return list(_games["rows"])
    body = _get(f"/modules?role=game&tag={DRILL_TAG}")
    rows = [m for m in (body.get("modules") or [])
            if DRILL_TAG in (m.get("tags") or [])]
    rows.sort(key=lambda m: m.get("name", ""))
    with _lock:
        _games["rows"], _games["ts"] = rows, time.time()
    return list(rows)


def rounds_of(game: Dict[str, Any]) -> int:
    """How many rounds a drill runs, off its declared `max_turns`."""
    for attr in (game.get("info") or {}).get("attributes") or []:
        if attr.get("name") == "max_turns":
            digits = re.findall(r"\d+", str(attr.get("value") or ""))
            if digits:
                return int(digits[0])
    return int((game.get("info") or {}).get("max_turns") or 6)


def forget() -> None:
    """Drop the caches. Only the tests and a fresh upload need this."""
    with _lock:
        _games["rows"], _games["ts"] = [], 0.0
        _sheets.clear()


# ── a table, walked to a round ───────────────────────────────────────

def _walk(game: str, seed: int, round: int) -> Dict[str, Any]:
    """Open a table and blank-move it forward to `round`.

    The blanks are not a trick played on the drill: an empty answer is illegal
    and scores nothing in every one of them, and a drill's questions do not
    depend on its answers. So the table arrives at round 4 with a clean slate
    and the same question, which is what makes "round 4" addressable at all.
    """
    opened = _rpc(game, "open", {"seats": 1, "seed": int(seed)})
    table = opened.get("table")
    if not table:
        raise Unavailable(f"the arena opened no table at {game}")
    state = opened.get("state") or {}
    for _ in range(max(0, int(round))):
        if state.get("done"):
            break
        state = (_rpc(game, "move", {"table": table, "seat": 0, "move": ""})
                 or {}).get("state") or {}
    return {"table": table, "state": state}


def sheet(game: str, seed: int = DEFAULT_SEED, round: int = 0) -> str:
    """The view a seat is shown at that round — the whole of what it gets.

    Cached forever by (game, seed, round): a drill is deterministic in its seed,
    so this text cannot change without the game's bytes changing, and its bytes
    are its id.
    """
    key = f"{game}@{seed}@{round}"
    with _lock:
        if key in _sheets:
            return _sheets[key]
    state = _walk(game, seed, round)["state"]
    views = state.get("views") or {}
    view = str(views.get("0") or views.get(0) or "")
    if not view.strip():
        raise Unavailable(f"{game} round {round} showed seat 0 nothing")
    with _lock:
        _sheets[key] = view
    return view


def grade(game: str, seed: int, round: int, answer: str) -> Dict[str, Any]:
    """Play one answer into a table at that game and read back how it went.

    Returns `{correct, legal, note, answer}`. The judgement is the arena's: we
    walk a table to the round, play the answer, and look at what the drill says
    afterwards — its own "Correct so far" line, or its result if that was the
    last round. Nothing here knows what the right answer was.
    """
    walked = _walk(game, seed, round)
    before = _correct_in(walked["state"])
    played = _rpc(game, "move",
                  {"table": walked["table"], "seat": 0, "move": str(answer)})
    state = played.get("state") or {}
    legal = played.get("legal")
    if isinstance(legal, dict):
        legal = legal.get("0", legal.get(0, True))

    after = _correct_in(state)
    if after is None and state.get("done"):
        # the last round: the drill has published its result, and the blanks
        # before this one can only have scored zero
        scores = ((state.get("result") or {}).get("scores") or [0])
        after = int(scores[0] or 0)
    return {
        "correct": bool(after is not None and before is not None and after > before),
        "legal": bool(legal) if legal is not None else True,
        "note": str(played.get("note") or ""),
        "answer": str(answer)[:400],
        "game": game, "seed": int(seed), "round": int(round),
    }


def _correct_in(state: Dict[str, Any]) -> Optional[int]:
    """The running score out of a drill's view: `Correct so far: 2 of 3.`

    Read out of the text on purpose. It is the one number every drill in the
    pack shows a seat, in a format they all share and state in their own
    docstrings, so this works for a drill written tomorrow by someone who never
    read this file.
    """
    views = state.get("views") or {}
    text = str(views.get("0") or views.get(0) or "")
    found = re.search(r"Correct so far:\s*(\d+)\s+of", text)
    return int(found.group(1)) if found else None


# ── as tasks on this board ───────────────────────────────────────────

def task_rounds(total: int, want: int) -> List[int]:
    """Which rounds of a drill join the pool.

    Spread across the ramp rather than taken from the front: a drill gets
    harder as it goes, and a pool of nothing but round 1 measures nothing but
    whether an agent can add two-digit numbers.
    """
    total, want = max(1, int(total)), max(1, int(want))
    if want >= total:
        return list(range(total))
    return sorted({round(i * (total - 1) / (want - 1)) if want > 1 else total - 1
                   for i in range(want)})


def as_arena_task(game: Dict[str, Any], round: int, seed: int = DEFAULT_SEED,
                  steps: int = None) -> Dict[str, Any]:
    """One round of one drill, as a task on this board."""
    name = game.get("name") or ""
    view = sheet(name, seed, round)
    return {
        "suite": SUITE,
        "key": f"{SUITE}#{name}@{round}",
        "title": f"{name} · round {round + 1}",
        "description": game.get("description", ""),
        "prompt": (
            f"You are sitting at `{name}`, a drill in the arena module. This is "
            f"the view your seat is shown, and it is the whole of what you get:\n\n"
            f"{view}\n\n"
            f"Write your answer — just the answer, in the form the view asked "
            f"for, with no explanation around it — to {{workdir}}/answer.txt.\n"
            f"It is played into the arena exactly as you leave it, and the "
            f"arena decides whether it stood."
        ),
        "steps": int(steps or DEFAULT_STEPS),
        "scorers": [{"type": SUITE, "game": name, "seed": int(seed),
                     "round": int(round), "path": "answer.txt"}],
    }


def pool(rounds: int = DEFAULT_ROUNDS, seed: int = DEFAULT_SEED,
         steps: int = None, limit: int = 0) -> List[Dict[str, Any]]:
    """The arena's drills as tasks. Never raises: a neighbour that is down
    means no drill tasks this round, not a round that fell over."""
    try:
        rows = games()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for game in rows:
        try:
            total = rounds_of(game)
            for r in task_rounds(total, rounds):
                out.append(as_arena_task(game, r, seed=seed, steps=steps))
        except Exception:
            # one unreadable drill is one drill missing from the pool
            continue
    return out[:limit] if limit else out


def task(key: str, seed: int = DEFAULT_SEED, steps: int = None) -> Optional[Dict[str, Any]]:
    """One drill task by key — `arena#addup@4` — fetched directly.

    Naming a task has to work past whatever the pool happened to include, the
    same way it does for the openarena bridge: a round nobody put in the pool is
    still a round that can be played.
    """
    body = key.split("#", 1)[1] if "#" in key else key
    name, _, tail = body.partition("@")
    if not name:
        return None
    try:
        row = next((g for g in games() if g.get("name") == name), None)
        if row is None:
            return None
        return as_arena_task(row, int(tail or 0), seed=seed, steps=steps)
    except Exception:
        return None
