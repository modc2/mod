#!/usr/bin/env python3
"""The class host — what `host.mjs` is for wasm, this is for a Python class.

One process, one class, one match. It reads JSON lines on stdin and writes
JSON lines on stdout:

    {"op": "load", "source": "...", "seed": 7}
    {"op": "call", "method": "view", "args": [0]}
    {"op": "close"}

and, in the other direction, one request this process makes of the host:

    {"mcp": {"server": "arena", "tool": "leaderboard", "arguments": {}}}
    → {"op": "mcp_result", "value": {...}}

Everything the wasm host promises, this promises the same way it can:

    no filesystem   RLIMIT_FSIZE is 0 and `open` is not in builtins
    no network      `socket`, `http`, `urllib`, `subprocess` are not importable
    seeded          `random` is seeded from the match seed before __init__ runs
    no wall clock   `time` and `datetime` are not importable, so a class cannot
                    read anything that would make a replay diverge
    bounded         address space, CPU seconds and file descriptors are capped

Said plainly, because it matters: **this is not the wasm sandbox.** Wasm cannot
reach anything the host does not hand it; CPython can be talked out of a
restricted namespace by anyone who knows the language well enough. The limits
here stop accidents and casual mischief, and the RLIMITs bound the damage of a
runaway. Run classes you would run on your own machine. Run strangers' wasm.

There is one way out, and it is not a hole in the above: `self.mcp(server,
tool, args)`. The class still cannot open a socket — it writes a line, and the
*host* makes the call, against a server the arena was configured with. What
that buys is a class that can consult a model, a database or another arena
mid-move; what it costs is that such a player's move is no longer a function of
its view alone. Both facts are worth saying out loud, so both are recorded: the
call goes through one place and every one of them is logged.

Anything the class prints lands in the match transcript, the way `arena.log`
does for a wasm module.
"""

import io
import json
import sys

# ── the cage ────────────────────────────────────────────────────────────────
# Set up before the class source is anywhere near this process, then drop the
# tools used to do it.

MEMORY_BYTES = 512 * 1024 * 1024
CPU_SECONDS = 30

def _limit():
    try:
        import resource
    except ImportError:          # not POSIX — the import allowlist still holds
        return {"rlimits": False}
    applied = {}
    for name, value in (
        ("RLIMIT_AS", MEMORY_BYTES),      # address space
        ("RLIMIT_CPU", CPU_SECONDS),      # SIGXCPU on a spin
        ("RLIMIT_FSIZE", 0),              # every write to a file fails
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_NPROC", 256),            # shared with the user, so kept loose
        ("RLIMIT_CORE", 0),
    ):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            soft, hard = resource.getrlimit(which)
            want = value if hard in (resource.RLIM_INFINITY, -1) else min(value, hard)
            resource.setrlimit(which, (want, hard))
            applied[name] = want
        except (ValueError, OSError):
            pass
    return applied


ALLOWED_IMPORTS = {
    "abc", "array", "bisect", "collections", "copy", "dataclasses", "decimal", "enum",
    "fractions", "functools", "hashlib", "heapq", "itertools", "json", "math", "operator",
    "queue", "random", "re", "statistics", "string", "textwrap", "types", "typing",
}

# The real stdout and stdin, kept aside at startup. `sys.stdout` is redirected
# into the transcript buffer, so anything that has to reach the host — the
# replies, and the MCP requests below — writes here instead.
_CHANNEL = {"out": None, "calls": 0}
MAX_MCP_CALLS = 64


def _mcp(server, tool="", arguments=None):
    """Call a tool on an MCP server, through the host.

    Returns whatever the server answered, as a dict — including
    `{"error": ...}`, which is a normal outcome and not an exception. A class
    that cannot lose this call is a class that will fail a match one day for
    reasons that have nothing to do with how it plays.
    """
    out = _CHANNEL["out"]
    if out is None:
        return {"error": "this class is not running under the arena host"}
    if _CHANNEL["calls"] >= MAX_MCP_CALLS:
        return {"error": f"this class has made its {MAX_MCP_CALLS} MCP calls for one match"}
    _CHANNEL["calls"] += 1
    request = {"mcp": {"server": str(server), "tool": str(tool),
                       "arguments": arguments if isinstance(arguments, dict) else {}}}
    out.write(json.dumps(request) + "\n")
    out.flush()
    line = sys.stdin.readline()
    if not line:
        return {"error": "the host closed while this class was waiting on an MCP call"}
    try:
        reply = json.loads(line)
    except ValueError as e:
        return {"error": f"the host sent back something unreadable: {e}"}
    return reply.get("value", {"error": "the host sent no answer"})


_real_import = __import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    top = name.split(".")[0]
    if top not in ALLOWED_IMPORTS:
        raise ImportError(
            f"the arena sandbox does not import `{name}` — a class here is pure computation. "
            f"Allowed: {', '.join(sorted(ALLOWED_IMPORTS))}"
        )
    return _real_import(name, globals, locals, fromlist, level)


# Builtins a class has no business calling. `eval`/`exec`/`compile` go because
# they are how a restricted namespace gets talked around; `open` and `input`
# because there is no filesystem and nobody at the keyboard.
DENIED_BUILTINS = {
    "open", "input", "exit", "quit", "help", "breakpoint",
    "eval", "exec", "compile", "memoryview", "globals", "vars",
}


def _namespace(seed):
    import builtins
    import random

    random.seed(seed)
    safe = {k: v for k, v in vars(builtins).items() if k not in DENIED_BUILTINS}
    safe["__import__"] = _guarded_import
    return {
        "__builtins__": safe,
        "__name__": "arena_class",
        "__doc__": None,
        # A class does not have to import `random` to get a seeded one.
        "random": random,
        "SEED": seed,
        # The one call that leaves this process. Also bound onto the instance
        # in `_construct`, so `self.mcp(...)` and a bare `mcp(...)` both work.
        "mcp": _mcp,
    }


# ── the ABI ─────────────────────────────────────────────────────────────────

GAME_METHODS = ("view", "step", "done", "result")
PLAYER_METHODS = ("play",)


def _role(obj):
    if all(callable(getattr(obj, m, None)) for m in GAME_METHODS):
        return "game"
    if all(callable(getattr(obj, m, None)) for m in PLAYER_METHODS):
        return "player"
    return "class"


def _pick(namespace, source):
    """The class to instantiate: the one that can be played, latest wins.

    A file may hold helpers. Definition order is the tie-break because it is
    the one a person reading top to bottom would also use.
    """
    rank = {"game": 3, "player": 2, "class": 1}
    found = []
    for name, value in namespace.items():
        if not isinstance(value, type):
            continue
        # Only classes this file defined — not one it imported.
        if getattr(value, "__module__", None) != "arena_class":
            continue
        found.append((rank[_role(value)], source.find(f"class {name}"), name, value))
    if not found:
        raise ValueError(
            "the source defines no class — one with view/step/done/result is a game, "
            "one with play is a player"
        )
    found.sort(key=lambda t: (t[0], t[1]))
    return found[-1][3]


def _construct(cls, seed):
    """Build the instance, passing the seed to whichever door it opened."""
    import inspect

    try:
        params = [p for p in inspect.signature(cls).parameters.values()]
    except (TypeError, ValueError):
        params = []
    positional = [p for p in params if p.kind in
                  (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    required = [p for p in positional if p.default is p.empty]
    if positional and (required or positional[0].name in ("seed", "rng")):
        obj = cls(seed)
    else:
        obj = cls()
    # A class that would rather take the seed in `init(seed)` may.
    init = getattr(obj, "init", None)
    if callable(init):
        try:
            init(seed)
        except TypeError:
            init()
    if not hasattr(obj, "seed"):
        try:
            obj.seed = seed
        except AttributeError:
            pass
    # A plain function on the instance, so `self.mcp(server, tool, args)` calls
    # it with the arguments the author wrote and no phantom `self`.
    if not hasattr(type(obj), "mcp"):
        try:
            obj.mcp = _mcp
        except AttributeError:
            pass
    return obj


def _info(obj):
    """A game's own description of itself, out of its attributes and docstring."""
    if callable(getattr(obj, "info", None)):
        given = obj.info()
        if isinstance(given, dict):
            return given
    players = getattr(obj, "players", None)
    if isinstance(players, (list, tuple)) and len(players) == 2:
        lo, hi = players
    else:
        lo = hi = players if isinstance(players, int) else 2
    doc = (type(obj).__doc__ or "").strip().split("\n")[0]
    return {
        "name": getattr(obj, "name", "") or type(obj).__name__,
        "description": getattr(obj, "description", "") or doc,
        "min_players": int(getattr(obj, "min_players", lo) or 1),
        "max_players": int(getattr(obj, "max_players", hi) or 1),
        "max_turns": int(getattr(obj, "max_turns", 200) or 200),
    }


def _normalise_step(returned, moves):
    """Make one shape out of the several a `step` may reasonably return.

    A game that returns nothing has accepted every move — the common case, and
    the one a first-time author writes without thinking about it.
    """
    legal, note = {}, ""
    if isinstance(returned, dict):
        # `{0: False, "note": "…"}` is what a game author actually writes, so
        # the note is lifted out and whatever is left is the verdict per seat.
        rest = dict(returned)
        note = rest.pop("note", "") or ""
        legal = rest.pop("legal", None) if "legal" in rest else rest
        legal = legal or {}
    elif isinstance(returned, bool):
        legal = {seat: returned for seat in moves}
    elif isinstance(returned, str):
        note = returned
    elif isinstance(returned, (list, tuple)):
        legal = {i: bool(v) for i, v in enumerate(returned)}
    return {"legal": {str(k): bool(v) for k, v in (legal or {}).items()}, "note": str(note)}


def _normalise_result(returned, seats):
    if not isinstance(returned, dict):
        scores = returned if isinstance(returned, (list, tuple)) else []
        returned = {"scores": list(scores)}
    scores = returned.get("scores") or []
    if isinstance(scores, dict):
        scores = [scores.get(i, scores.get(str(i), 0)) for i in range(seats or len(scores))]
    out = dict(returned)
    out["scores"] = [float(s or 0) for s in scores]
    out["summary"] = str(returned.get("summary") or "")
    return out


def _normalise_turn(returned, seats, turn_no):
    if returned is None:
        return [turn_no % seats] if seats else []
    if isinstance(returned, int) and not isinstance(returned, bool):
        returned = [returned]
    if isinstance(returned, dict):
        returned = returned.get("seats", [])
    return [int(s) for s in returned if isinstance(s, (int, float)) and 0 <= int(s) < seats]


# ── the loop ────────────────────────────────────────────────────────────────

class Session:
    def __init__(self):
        self.obj = None
        self.seats = 0

    def load(self, msg):
        source = msg.get("source") or ""
        seed = int(msg.get("seed") or 1)
        self.seats = int(msg.get("seats") or 0)
        ns = _namespace(seed)
        code = compile(source, "<class>", "exec")   # this file's compile, not the class's
        exec(code, ns)                              # noqa: S102 — the whole point
        cls = _pick(ns, source)
        self.obj = _construct(cls, seed)
        role = _role(self.obj)
        out = {"class": cls.__name__, "role": role,
               "methods": sorted(m for m in dir(self.obj)
                                 if not m.startswith("_") and callable(getattr(self.obj, m, None)))}
        if role == "game":
            out["info"] = _info(self.obj)
        return out

    def call(self, msg):
        if self.obj is None:
            raise ValueError("nothing is loaded")
        method = msg.get("method") or ""
        args = msg.get("args") or []

        if method == "info":
            return {"value": _info(self.obj)}
        if method == "turn":
            seats, turn_no = int(args[0] if args else self.seats), int(args[1] if len(args) > 1 else 0)
            fn = getattr(self.obj, "turn", None)
            got = fn() if callable(fn) else None
            return {"value": _normalise_turn(got, seats, turn_no)}
        if method == "step":
            moves = args[0] if args else {}
            # Seats are integers to a Python author; they arrive over JSON as
            # strings. Hand over both, so `moves[0]` and `moves["0"]` both work.
            keyed = {}
            for k, v in (moves or {}).items():
                keyed[int(k)] = v
                keyed[str(k)] = v
            got = self.obj.step(keyed)
            return {"value": _normalise_step(got, [k for k in keyed if isinstance(k, int)])}
        if method == "result":
            return {"value": _normalise_result(self.obj.result(), self.seats)}
        if method == "done":
            return {"value": bool(self.obj.done())}

        fn = getattr(self.obj, method, None)
        if not callable(fn):
            raise ValueError(f"the class defines no `{method}`")
        value = fn(*args)
        if method in ("view", "play"):
            value = "" if value is None else str(value)
        return {"value": value}


def main():
    limits = _limit()
    session = Session()
    out = sys.stdout
    _CHANNEL["out"] = out
    # Whatever the class prints belongs in the transcript, not in the protocol.
    printed = io.StringIO()
    sys.stdout = printed

    def reply(payload):
        out.write(json.dumps(payload) + "\n")
        out.flush()

    reply({"ok": True, "hello": "arena class host", "python": sys.version.split()[0],
           "limits": limits, "allowed_imports": sorted(ALLOWED_IMPORTS),
           "mcp": True})

    # `readline` rather than iterating stdin: an MCP call reads a line from the
    # middle of handling one, and the iterator's read-ahead buffer would eat
    # the answer before this loop ever saw it.
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as e:
            reply({"ok": False, "error": f"bad request: {e}"})
            continue
        op = msg.get("op")
        if op == "close":
            break
        try:
            if op == "load":
                result = session.load(msg)
            elif op == "call":
                result = session.call(msg)
            elif op == "ping":
                result = {}
            else:
                raise ValueError(f"unknown op `{op}`")
            log = printed.getvalue()
            printed.seek(0)
            printed.truncate(0)
            reply({"ok": True, **result, **({"log": log} if log else {})})
        except Exception as e:                      # noqa: BLE001 — reported, not raised
            log = printed.getvalue()
            printed.seek(0)
            printed.truncate(0)
            detail = f"{type(e).__name__}: {e}"
            reply({"ok": False, "error": detail, **({"log": log} if log else {})})


if __name__ == "__main__":
    main()
