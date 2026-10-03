"""
openarena - the agent arena speaks the openarena task schema

The board next door scores a *trace*: did the agent create the file, avoid the
tool, finish clean. openarena (orbit/openarena) scores a *program*: a task
there is a statement plus a set of graded cases, some of them hidden, and its
judge runs the submission in a throwaway sandbox and reports every case. Both
are worth having and neither answers the other's question, so this file makes
them one board instead of two.

An openarena task joins the pool under the suite `openarena`:

    key       openarena#fizzbuzz
    prompt    the brief openarena hands its own competitors, plus where to
              leave the program — the scratch dir the arena already made
    scorers   [{"type": "openarena", "task": "fizzbuzz", "path": "solution.py"}]

At scoring time the `openarena` scorer reads the program out of the scratch dir
and POSTs it to openarena's /submit, which grades it against every case,
including the hidden ones this module never sees. We run the agents; openarena
grades the code. Neither judge is reimplemented here, and a task written from
the agent console is stored in openarena's own registry — so it is the same
task openarena's own competitors play, not a copy that drifts.

    from src.arena import openarena as oa
    oa.available()                     # is the arena next door up?
    oa.pool(limit=24)                  # its tasks, as agent-arena tasks
    oa.grade("fizzbuzz", code, "python")
    oa.create_task({...})              # openarena schema in, stored there
    oa.bench_import("humaneval", limit=20)

Everything here is best-effort: openarena is an optional neighbour, so a
module that is down or absent means "no openarena tasks in the pool", never a
traceback in a round.
"""
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # the neighbour is optional, and so is its client
    requests = None


# where openarena answers, unless something says otherwise. Its own console
# talks to the same port, so this is the one place the address is written down
DEFAULT_BASE = "http://127.0.0.1:50400"

SUITE = "openarena"

# a coding task is write-then-check, so it needs more room than the board's
# 8-step default — the agent has to produce a file and usually runs it once
DEFAULT_STEPS = 10

# The list is cheap and changes (anyone can upload); task bodies do not — a
# task in openarena is immutable once created (the slug is taken, and there is
# no update path), so a body fetched once is a body forever.
LIST_TTL = 30.0
MAX_CACHED = 2000

# how long we wait on each surface. Grading runs real programs against every
# case, so it gets the judge's own budget rather than a browser's patience.
T_LIST = 5.0
T_GRADE = 180.0
T_IMPORT = 240.0

_lock = threading.Lock()
_bodies: Dict[str, Dict[str, Any]] = {}
_list: Dict[str, Any] = {"ts": 0.0, "rows": []}


class Unavailable(RuntimeError):
    """openarena could not be reached — a judge that is down is not a failed
    agent, so callers turn this into a void match rather than a lost one."""


# ── where it lives ───────────────────────────────────────────────────

def base() -> str:
    """openarena's API root: the environment, then its config, then the default."""
    env = (os.environ.get("OPENARENA_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        # ../openarena/config.json — the fleet keeps modules side by side
        cfg = Path(__file__).resolve().parents[3] / "openarena" / "config.json"
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
        raise Unavailable(f"openarena unreachable at {base()}: {e}")
    if r.status_code >= 400:
        raise Unavailable(f"openarena {path} -> {r.status_code} {r.text[:160]}")
    return r.json()


def _send(method: str, path: str, body: Dict[str, Any] = None,
          timeout: float = T_LIST) -> Any:
    if requests is None:
        raise Unavailable("python `requests` is not installed")
    try:
        r = requests.request(method, f"{base()}{path}", json=body or {}, timeout=timeout)
    except Exception as e:
        raise Unavailable(f"openarena unreachable at {base()}: {e}")
    try:
        out = r.json()
    except Exception:
        raise Unavailable(f"openarena {path} -> {r.status_code} {r.text[:160]}")
    # openarena answers errors as {"error": "..."} with a 4xx — that is the
    # author's mistake, not an outage, so it comes back as a value
    if r.status_code >= 400 and not isinstance(out, dict):
        raise Unavailable(f"openarena {path} -> {r.status_code}")
    return out


def info() -> Dict[str, Any]:
    """openarena's own info document — raises Unavailable if it is not up."""
    return _get("/")


def available() -> bool:
    try:
        info()
        return True
    except Exception:
        return False


def status() -> Dict[str, Any]:
    """What the console needs to decide whether to offer any of this."""
    try:
        d = info()
        return {
            "available": True, "base": base(),
            "version": d.get("version"), "tasks": d.get("tasks"),
            "agents": d.get("agents"), "matches": d.get("matches"),
            "languages": d.get("languages") or LANGUAGES,
            "sources": (d.get("benchmarks") or d.get("bench_sources")
                        or BENCH_SOURCES),
            "console": f"{base()}/openarena",
        }
    except Exception as e:
        return {"available": False, "base": base(), "error": str(e)}


# ── the schema, as this module needs to know it ───────────────────────

LANGUAGES = ("python", "javascript", "bash")
MODES = ("io", "unit")
BENCH_SOURCES = ("humaneval", "humanevalplus", "mbpp", "code_contests",
                 "hf", "json", "html")

_ENTRYPOINTS = {
    "python": "solution.py", "python3": "solution.py", "py": "solution.py",
    "javascript": "solution.js", "js": "solution.js", "node": "solution.js",
    "bash": "solution.sh", "sh": "solution.sh", "shell": "solution.sh",
}


def entrypoint(language: str) -> str:
    """The filename a submission is saved as — part of the grading contract,
    since a `unit` task's graders import it by name."""
    return _ENTRYPOINTS.get(str(language or "").strip().lower(), "solution.py")


def brief(task: Dict[str, Any], workfile: str = None) -> str:
    """What the competitor is told.

    The same brief openarena writes for its own entrants (statement, language,
    the submission contract and the visible examples — never a hidden case),
    plus the one thing that differs here: our competitors hold tools and a
    scratch dir, so they are told to leave the program in a file. The fenced
    block stays welcome as a fallback, which is what makes the same agent
    playable from either board.
    """
    lang = str(task.get("language") or "any")
    mode = str(task.get("mode") or "io")
    entry = workfile or entrypoint(lang)
    spoken = ("any language this arena runs (python, javascript or bash)"
              if lang in ("", "any") else lang)

    out = [f"# {task.get('title', '')}", "", str(task.get("statement") or ""), "",
           f"Language: {spoken}."]
    if mode == "unit":
        out.append(f"Your program is imported by hidden graders, so define everything "
                   f"at module level and print nothing on import.")
    else:
        out.append("Your program reads its input from stdin and writes the answer to "
                   "stdout. Nothing else may be printed.")
    out.append(f"Write the complete program to `{entry}` in your working directory. "
               f"That file is what gets graded.")

    starter = str(task.get("starter") or "")
    if starter:
        out += ["", "Starter code:", "```", starter, "```"]

    cases = [c for c in (task.get("tests") or []) if not c.get("hidden")]
    if cases and mode != "unit":
        out.append("")
        out.append("Examples:")
        for c in cases[:3]:
            out += ["", f"--- {c.get('name', '')} ---", "input:",
                    str(c.get("stdin") or ""), "expected output:",
                    str(c.get("expect") or "")]

    total = len(task.get("tests") or [])
    hidden = sum(1 for c in (task.get("tests") or []) if c.get("hidden"))
    out += ["", f"There are {total} graded cases in total, {hidden} of them hidden. "
                f"Only the program is graded — nothing you say counts."]
    return "\n".join(out)


# ── tasks ────────────────────────────────────────────────────────────

def list_tasks(**filters) -> List[Dict[str, Any]]:
    """The task index, as openarena summarises it (no statement, no cases)."""
    q = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    data = _get(f"/tasks{'?' + q if q else ''}")
    return list(data.get("tasks") or [])


def get_task(slug: str, cached: bool = True) -> Dict[str, Any]:
    """One task in full, as an entrant may see it — hidden cases stay hidden."""
    slug = str(slug or "").strip()
    if cached:
        with _lock:
            hit = _bodies.get(slug)
        if hit:
            return hit
    body = _get(f"/tasks/{slug}")
    if not isinstance(body, dict) or body.get("error"):
        raise Unavailable(f"openarena task {slug}: {(body or {}).get('error')}")
    with _lock:
        if len(_bodies) >= MAX_CACHED:
            _bodies.clear()
        _bodies[slug] = body
        _bodies[str(body.get("id") or "")] = body
    return body


def forget(slug: str = None) -> None:
    """Drop what we remember of a task body — a deleted or replaced task."""
    with _lock:
        if slug is None:
            _bodies.clear()
        else:
            _bodies.pop(str(slug), None)
        _list["ts"] = 0.0


def index(ttl: float = LIST_TTL) -> List[Dict[str, Any]]:
    """The task index, cached briefly: the pool is rebuilt on every UI poll."""
    import time
    with _lock:
        if _list["rows"] and (time.time() - float(_list["ts"])) < ttl:
            return list(_list["rows"])
    rows = list_tasks()
    with _lock:
        _list["rows"], _list["ts"] = rows, time.time()
    return rows


def as_arena_task(body: Dict[str, Any], steps: int = None) -> Dict[str, Any]:
    """One openarena task as an agent-arena task.

    The grading contract survives the translation intact: the scorer names the
    task by slug and the file to submit, and openarena's judge does the rest.
    """
    slug = str(body.get("slug") or body.get("id") or "")
    lang = str(body.get("language") or "any")
    entry = entrypoint(lang)
    return {
        "key": f"{SUITE}#{slug}",
        "suite": SUITE,
        "index": slug,
        "title": body.get("title") or slug,
        "description": " ".join(str(body.get("statement") or "").split())[:280],
        "prompt": brief(body, workfile=entry),
        "scorers": [{"type": "openarena", "task": slug, "path": entry,
                     "language": lang}],
        "setup": {"files": {entry: body.get("starter") or ""}} if body.get("starter") else {},
        "steps": int(steps or DEFAULT_STEPS),
        # what the board shows about a task it did not write
        "openarena": {
            "slug": slug, "id": body.get("id"), "mode": body.get("mode"),
            "language": lang, "cases": body.get("total_tests") or len(body.get("tests") or []),
            "hidden": body.get("hidden_tests") or 0,
            "tags": body.get("tags") or [], "author": body.get("author") or "",
        },
    }


def pool(limit: int = 0, steps: int = None) -> List[Dict[str, Any]]:
    """openarena's tasks as arena tasks, newest first. Never raises.

    `limit` caps how much of a big import joins the pool: a 500-task benchmark
    is a fine thing to hold and a bad thing to make every round walk through.
    """
    try:
        rows = index()
    except Exception:
        return []
    if limit and limit > 0:
        rows = sorted(rows, key=lambda t: -float(t.get("created") or 0))[:int(limit)]
    out = []
    for row in rows:
        slug = row.get("slug") or row.get("id")
        if not slug:
            continue
        try:
            out.append(as_arena_task(get_task(slug), steps=steps))
        except Exception:
            continue      # one unreadable task is not an empty pool
    return out


def create_task(spec: Dict[str, Any], author: str = None) -> Dict[str, Any]:
    """Upload a task in the openarena schema. Validation is openarena's."""
    body = validate(spec)
    if author:
        body["author"] = author
    out = _send("POST", "/tasks", body)
    forget()
    if isinstance(out, dict) and out.get("error"):
        raise ValueError(str(out["error"]))
    return out


def delete_task(slug: str) -> Dict[str, Any]:
    out = _send("DELETE", f"/tasks/{slug}")
    forget(slug)
    if isinstance(out, dict) and out.get("error"):
        raise ValueError(str(out["error"]))
    return out


def validate(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Check a task spec here so a bad one fails with a readable message
    instead of a 400 from a neighbour the author never called.

    openarena validates again on the way in — this is the friendly copy, not
    the authority.
    """
    title = str(spec.get("title") or "").strip()
    if not title:
        raise ValueError("a task needs a title")
    statement = str(spec.get("statement") or spec.get("prompt") or "").strip()
    if len(statement) < 12:
        raise ValueError("a task needs a statement — describe what the program must do")
    mode = str(spec.get("mode") or "io").strip().lower()
    if mode not in MODES:
        raise ValueError(f"unknown mode `{mode}` — expected io or unit")
    lang = str(spec.get("language") or "any").strip().lower() or "any"
    if lang != "any" and lang not in _ENTRYPOINTS:
        raise ValueError(f"unsupported language `{lang}` — openarena runs "
                         f"{', '.join(LANGUAGES)}")
    cases = spec.get("tests") or []
    if not isinstance(cases, list) or not cases:
        raise ValueError("a task needs at least one test case — a task nobody can "
                         "fail is not a task")
    clean = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            raise ValueError("each test case must be an object")
        case = {
            "name": str(c.get("name") or f"case {i + 1}").strip()[:80],
            "hidden": bool(c.get("hidden")),
            "weight": float(c.get("weight") or 1.0),
        }
        if mode == "unit":
            program = str(c.get("program") or "").strip()
            if not program:
                raise ValueError(f"case `{case['name']}`: a unit case needs a "
                                 f"`program` that imports the submission")
            case["program"] = program
        else:
            expect = c.get("expect")
            if expect is None or str(expect).strip() == "":
                raise ValueError(f"case `{case['name']}`: an io case needs the "
                                 f"`expect`ed stdout")
            case["stdin"] = str(c.get("stdin") or "")
            case["expect"] = str(expect)
            compare = str(c.get("compare") or "trim").strip().lower()
            if compare not in ("trim", "exact", "contains"):
                raise ValueError(f"case `{case['name']}`: compare must be trim, "
                                 f"exact or contains")
            case["compare"] = compare
        clean.append(case)
    if all(c["hidden"] for c in clean):
        raise ValueError("at least one case must be visible — an entrant needs "
                         "something to check its answer against")
    out = {
        "title": title[:120], "statement": statement, "mode": mode,
        "language": lang, "tests": clean,
        "starter": str(spec.get("starter") or ""),
        "tags": [str(t).strip()[:24] for t in (spec.get("tags") or []) if str(t).strip()][:8],
    }
    if spec.get("slug"):
        out["slug"] = str(spec["slug"]).strip()
    if spec.get("timeout_ms"):
        out["timeout_ms"] = int(spec["timeout_ms"])
    return out


# ── grading ──────────────────────────────────────────────────────────

def grade(slug: str, code: str, language: str = "", agent: str = None) -> Dict[str, Any]:
    """Submit a program to openarena's judge and read every case back.

    Unrated over there — openarena only moves its own Elo when it raced the
    entrants itself. The score comes home and moves the score here.
    """
    if not str(code or "").strip():
        raise ValueError("nothing to grade — the run produced no program")
    body = {"task": slug, "code": code, "language": language or ""}
    if agent:
        body["agent"] = agent
    out = _send("POST", "/submit", body, timeout=T_GRADE)
    if not isinstance(out, dict):
        raise Unavailable("openarena /submit returned something unreadable")
    if out.get("error"):
        # "no task X" is a broken task spec, not an outage — but from a match's
        # point of view both mean the same thing: this was not scoreable
        raise Unavailable(str(out["error"]))
    return out


# ── pulling a program out of a run ───────────────────────────────────

_FENCE = re.compile(r"^\s*(?:```|~~~)(\w*)\s*$")
_CODE_MARKS = ("def ", "import ", "function ", "const ", "let ", "print(",
               "console.log", "#!/", "class ")


def fenced(text: str) -> Tuple[str, str]:
    """The last fenced block in a reply, and the language it announced.

    Mirrors openarena's own extraction, so the same agent answering the same
    way is read the same way on either board.
    """
    best: Optional[Tuple[str, str]] = None
    buf: List[str] = []
    lang, open_ = "", False
    for line in str(text or "").splitlines():
        m = _FENCE.match(line)
        if m:
            if open_:
                best = ("\n".join(buf), lang)
                buf, open_ = [], False
            else:
                lang, open_ = (m.group(1) or "").lower(), True
            continue
        if open_:
            buf.append(line)
    if open_ and buf and best is None:
        best = ("\n".join(buf), lang)
    if best is None:
        return "", ""
    return best[0].strip("\n"), best[1]


def looks_like_code(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and any(m in t for m in _CODE_MARKS)


def code_from_trace(steps: List[Dict[str, Any]]) -> Tuple[str, str]:
    """The program an agent showed rather than wrote to a file.

    Newest first, and a fence anywhere beats a bare blob. Never concatenates:
    splicing prose into a submission fails it at the judge for a reason the
    entrant never caused.
    """
    parts: List[str] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        for field in ("result", "summary", "response", "content", "text"):
            v = step.get(field)
            if isinstance(v, str) and v.strip():
                parts.append(v)
        params = step.get("params")
        if isinstance(params, dict):
            for field in ("content", "response", "summary", "text", "body"):
                v = params.get(field)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
    for part in reversed(parts):
        code, lang = fenced(part)
        if code.strip():
            return code, lang
    for part in reversed(parts):
        if looks_like_code(part):
            return part.strip(), ""
    return "", ""


def pick_language(hint: str, task_language: str) -> str:
    """A ```python fence is only worth taking when openarena can run it."""
    hint = str(hint or "").strip().lower()
    if hint and hint != "any" and hint in _ENTRYPOINTS:
        return hint
    lang = str(task_language or "").strip().lower()
    if lang and lang != "any":
        return lang
    return "python"


# ── competing over there ─────────────────────────────────────────────

def enter(agent: str, name: str = None, base_url: str = None,
          **config) -> Dict[str, Any]:
    """Enter one of this module's agents as an openarena competitor.

    The other direction of the same integration: instead of pulling openarena's
    tasks onto our board, this puts our agent on theirs, where it is raced
    against every other entrant on the same task at the same moment.
    """
    agent = str(agent or "").strip()
    if not agent:
        raise ValueError("name the agent to enter")
    cfg = {"agent": agent, **{k: v for k, v in config.items() if v is not None}}
    if base_url:
        cfg["base"] = base_url
    out = _send("POST", "/agents", {
        "name": name or agent, "kind": "agent_mod",
        "description": f"`{agent}` from the fleet's agent module",
        "config": cfg,
    })
    if isinstance(out, dict) and out.get("error"):
        raise ValueError(str(out["error"]))
    return out


def entrants() -> List[Dict[str, Any]]:
    return list((_get("/agents") or {}).get("agents") or [])


def leaderboard() -> Dict[str, Any]:
    return _get("/leaderboard")


# ── benchmarks off the web ───────────────────────────────────────────

def bench_sources() -> Dict[str, Any]:
    return _get("/bench/sources")


def bench_preview(source: str, **opts) -> Dict[str, Any]:
    return _send("POST", "/bench/preview",
                 {"source": source, **opts}, timeout=T_IMPORT)


def bench_import(source: str, **opts) -> Dict[str, Any]:
    out = _send("POST", "/bench/import", {"source": source, **opts},
                timeout=T_IMPORT)
    forget()
    if isinstance(out, dict) and out.get("error"):
        raise ValueError(str(out["error"]))
    return out
