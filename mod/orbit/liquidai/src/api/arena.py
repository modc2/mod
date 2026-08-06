"""ARENA — small models, small games, scored by a rule rather than a vibe.

An LFM2.5-350M is too small to be judged by "which answer sounds better", and
too fast to be worth judging by hand. So a game here is a list of rounds, each
with a prompt and a check the answer either passes or it doesn't:

    contains   the answer has to contain this text (case-insensitive)
    equals     stripped of punctuation and case, the answer is exactly this
    number     the first number in the answer equals this one
    regex      a pattern the answer has to match
    lines      the answer has exactly N non-empty lines
    absent     the answer must NOT contain this — for refusal and hygiene rounds

Deterministic scoring means two runs of the same model on the same game are
comparable, which is the only thing that makes a leaderboard mean anything. It
also means anyone can write a game: no judge model, no rubric, no API key —
just rounds and checks, saved to ~/.mod/liquidai/games.json.

Built-in games ship in code and can't be deleted; yours live in the store next
to them and are yours to edit.
"""

import json
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional

STORE = os.path.expanduser("~/.mod/liquidai")
GAMES_PATH = os.path.join(STORE, "games.json")
RESULTS_PATH = os.path.join(STORE, "arena.json")

MAX_ROUNDS = 12
MAX_RESULTS = 400
CHECKS = ("contains", "equals", "number", "regex", "lines", "absent")


# ── the games that ship ──────────────────────────────────────────────

BUILTIN: List[Dict[str, Any]] = [
    {
        "id": "extract",
        "name": "EXTRACT",
        "blurb": "Pull the field out of the sentence and say nothing else.",
        "system": "You extract data. Answer with the value only — no sentence, no label.",
        "max_tokens": 32,
        "rounds": [
            {"prompt": "Invoice 4471, $2,300 due 14 March. What is the amount due?",
             "check": "number", "expect": "2300"},
            {"prompt": "From 'Ada Lovelace, born 1815, London' — what year?",
             "check": "number", "expect": "1815"},
            {"prompt": "From 'ship it by Friday to 22 Baker St' — what street?",
             "check": "contains", "expect": "Baker"},
            {"prompt": "Email in this line: ping me at sam@modc2.com tomorrow.",
             "check": "contains", "expect": "sam@modc2.com"},
        ],
    },
    {
        "id": "arithmetic",
        "name": "MATH DASH",
        "blurb": "Four sums a phone could do. Small models often can't.",
        "system": "Answer with the number alone.",
        "max_tokens": 24,
        "rounds": [
            {"prompt": "17 + 25 = ?", "check": "number", "expect": "42"},
            {"prompt": "A box holds 12 eggs. How many eggs in 7 boxes?",
             "check": "number", "expect": "84"},
            {"prompt": "Half of 156 is?", "check": "number", "expect": "78"},
            {"prompt": "9 * 9 - 9 = ?", "check": "number", "expect": "72"},
        ],
    },
    {
        "id": "format",
        "name": "FOLLOW ORDERS",
        "blurb": "Instruction-following, measured in lines and words rather than taste.",
        "system": "Follow the format exactly. Never explain yourself.",
        "max_tokens": 96,
        "rounds": [
            {"prompt": "List exactly three colours, one per line, nothing else.",
             "check": "lines", "expect": "3"},
            {"prompt": "Reply with the single word: ACKNOWLEDGED",
             "check": "equals", "expect": "ACKNOWLEDGED"},
            {"prompt": "Answer only in valid JSON: {\"ok\": true}",
             "check": "regex", "expect": "\\{\\s*\"ok\"\\s*:\\s*true\\s*\\}"},
            {"prompt": "Say the capital of France without using the letter 'a'.",
             "check": "absent", "expect": "a"},
        ],
    },
    {
        "id": "translate",
        "name": "PHRASEBOOK",
        "blurb": "One line into another language — the LFMs are trained multilingual.",
        "system": "Translate. Output the translation only.",
        "max_tokens": 48,
        "rounds": [
            {"prompt": "Translate to French: the small model is enough.",
             "check": "regex", "expect": "(?i)petit|mod[èe]le"},
            {"prompt": "Translate to Spanish: where is the station?",
             "check": "regex", "expect": "(?i)estaci[oó]n"},
            {"prompt": "Translate to German: thank you very much.",
             "check": "regex", "expect": "(?i)danke"},
            {"prompt": "Translate to Japanese: good morning.",
             "check": "regex", "expect": "[\\u3040-\\u30ff\\u4e00-\\u9faf]"},
        ],
    },
]


# ── store ────────────────────────────────────────────────────────────

def _read(path: str, default: Any) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write(path: str, value: Any):
    os.makedirs(STORE, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def games() -> List[Dict[str, Any]]:
    """Built-ins first, then whatever's been written here."""
    mine = _read(GAMES_PATH, [])
    return [{**g, "builtin": True} for g in BUILTIN] + \
           [{**g, "builtin": False} for g in mine]


def game(game_id: str) -> Optional[Dict[str, Any]]:
    for g in games():
        if g["id"] == game_id:
            return g
    return None


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """A game is only worth saving if every round can actually be scored."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("a game needs a name")
    rounds = payload.get("rounds") or []
    if not rounds:
        raise ValueError("a game needs at least one round")
    if len(rounds) > MAX_ROUNDS:
        raise ValueError(f"{MAX_ROUNDS} rounds is the ceiling")

    clean = []
    for i, rnd in enumerate(rounds, 1):
        prompt = (rnd.get("prompt") or "").strip()
        check = (rnd.get("check") or "contains").strip()
        expect = str(rnd.get("expect") or "").strip()
        if not prompt:
            raise ValueError(f"round {i} has no prompt")
        if check not in CHECKS:
            raise ValueError(f"round {i}: check must be one of {', '.join(CHECKS)}")
        if not expect:
            raise ValueError(f"round {i} has nothing to check against")
        if check == "regex":
            try:
                re.compile(expect)
            except re.error as e:
                raise ValueError(f"round {i}: {e}")
        if check == "lines" and not expect.isdigit():
            raise ValueError(f"round {i}: 'lines' expects a count")
        clean.append({"prompt": prompt, "check": check, "expect": expect})

    return {
        "name": name[:40],
        "blurb": (payload.get("blurb") or "").strip()[:160],
        "system": (payload.get("system") or "").strip()[:500],
        "max_tokens": max(8, min(int(payload.get("max_tokens") or 96), 512)),
        "rounds": clean,
    }


def save(payload: Dict[str, Any], author: str) -> Dict[str, Any]:
    entry = validate(payload)
    mine = _read(GAMES_PATH, [])
    game_id = (payload.get("id") or "").strip()

    if game_id:
        if any(g["id"] == game_id for g in BUILTIN):
            raise ValueError("built-in games can't be edited — save a copy instead")
        for i, existing in enumerate(mine):
            if existing["id"] == game_id:
                if existing.get("author") not in (author, None):
                    raise ValueError("that game belongs to another account")
                mine[i] = {**existing, **entry, "id": game_id,
                           "updated_at": time.time()}
                _write(GAMES_PATH, mine)
                return {**mine[i], "builtin": False}
        raise ValueError(f"no game called {game_id}")

    entry.update(id=uuid.uuid4().hex[:8], author=author, created_at=time.time())
    mine.append(entry)
    _write(GAMES_PATH, mine)
    return {**entry, "builtin": False}


def delete(game_id: str, author: str, is_owner: bool = False) -> Dict[str, Any]:
    if any(g["id"] == game_id for g in BUILTIN):
        raise ValueError("built-in games can't be deleted")
    mine = _read(GAMES_PATH, [])
    keep = [g for g in mine
            if not (g["id"] == game_id and (is_owner or g.get("author") == author))]
    if len(keep) == len(mine):
        raise ValueError("no such game, or it isn't yours")
    _write(GAMES_PATH, keep)
    return {"deleted": game_id}


def fork(game_id: str, author: str) -> Dict[str, Any]:
    """Copy a game into your own list — how you edit a built-in."""
    src = game(game_id)
    if not src:
        raise ValueError(f"no game called {game_id}")
    return save({**{k: src[k] for k in ("name", "blurb", "system", "max_tokens", "rounds")},
                 "name": f"{src['name']} ⑂"[:40]}, author)


# ── scoring ──────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def score_answer(answer: str, check: str, expect: str) -> Dict[str, Any]:
    """One round, one boolean, and the reason it went that way."""
    got = (answer or "").strip()
    if check == "contains":
        ok = expect.lower() in got.lower()
    elif check == "absent":
        ok = expect.lower() not in got.lower()
    elif check == "equals":
        ok = _norm(got) == _norm(expect)
    elif check == "number":
        # Any number in the answer, not the first: a model that shows its
        # working writes "1/2 × 156 = 78" and the first number it emits is a 1.
        # Marking that wrong measures verbosity, not arithmetic. The prompt's
        # own numbers are the false-positive risk, and they're a different
        # value from the answer in any round worth asking.
        try:
            want = float(expect)
            ok = any(float(n) == want for n in _NUM_RE.findall(got.replace(",", "")))
        except ValueError:
            ok = False
    elif check == "lines":
        ok = len([l for l in got.splitlines() if l.strip()]) == int(expect)
    elif check == "regex":
        ok = re.search(expect, got) is not None
    else:
        ok = False
    return {"ok": ok, "check": check, "expect": expect}


# ── running a match ──────────────────────────────────────────────────

def play(game_id: str, model: str, runner: Callable[..., Iterator[Dict[str, Any]]],
         label: Optional[str] = None) -> Dict[str, Any]:
    """Walk one model through one game. `runner` is the runtime's generate().

    Every round is its own conversation: carrying history would let round 3
    ride on round 1's luck, and the point of the score is that each round is an
    independent question.
    """
    spec = game(game_id)
    if not spec:
        raise ValueError(f"no game called {game_id}")

    started = time.time()
    rounds: List[Dict[str, Any]] = []
    for rnd in spec["rounds"]:
        messages = ([{"role": "system", "content": spec["system"]}]
                    if spec.get("system") else [])
        messages.append({"role": "user", "content": rnd["prompt"]})

        answer, error, stats = "", None, {}
        for event in runner(model, messages, spec.get("max_tokens", 96), 0.0, 1.0):
            if event.get("type") == "token":
                answer += event["text"]
            elif event.get("type") == "done":
                stats = event
            elif event.get("type") == "error":
                error = event.get("error")
        verdict = score_answer(answer, rnd["check"], rnd["expect"])
        rounds.append({"prompt": rnd["prompt"], "answer": answer.strip(),
                       "error": error, "elapsed_sec": stats.get("elapsed_sec"),
                       **verdict})

    passed = sum(1 for r in rounds if r["ok"])
    elapsed = round(time.time() - started, 2)
    result = {
        "id": uuid.uuid4().hex[:8],
        "game": game_id,
        "game_name": spec["name"],
        "model": model,
        "label": label or model.split("/")[-1],
        "passed": passed,
        "total": len(rounds),
        "score": round(100 * passed / len(rounds)),
        "elapsed_sec": elapsed,
        "sec_per_round": round(elapsed / len(rounds), 2),
        "at": time.time(),
        "rounds": rounds,
    }
    record(result)
    return result


def record(result: Dict[str, Any]):
    """Keep the tally, not the transcripts — a board doesn't need the prose."""
    history = _read(RESULTS_PATH, [])
    history.append({k: v for k, v in result.items() if k != "rounds"})
    _write(RESULTS_PATH, history[-MAX_RESULTS:])


def leaderboard(game_id: Optional[str] = None) -> Dict[str, Any]:
    """Best run per (model, game), because a model's ceiling is the story."""
    history = _read(RESULTS_PATH, [])
    if game_id:
        history = [h for h in history if h["game"] == game_id]

    best: Dict[str, Dict[str, Any]] = {}
    for run in history:
        key = f"{run['model']}::{run['game']}"
        prior = best.get(key)
        if not prior or (run["score"], -run["sec_per_round"]) > \
                (prior["score"], -prior["sec_per_round"]):
            best[key] = run

    rows = sorted(best.values(),
                  key=lambda r: (-r["score"], r["sec_per_round"]))
    return {"count": len(rows), "runs": len(history), "rows": rows,
            "games": [{"id": g["id"], "name": g["name"]} for g in games()]}
