"""
formats - what an uploaded library file looks like

One parser behind every upload path (the console's drop zone, POST
/library/upload, forward(action="upload")). It turns a file's text into a
normalized library item, so a prompt, a skill, a memory note and a whole agent
can all be dragged into the same box.

Two carriers, both plain text:
    JSON      {"type": "agent", "name": "...", "goal": "..."}
    Markdown  optional YAML front matter, then the body

Kind resolution — first hit wins:
    1. the kind the caller picked (UI selector / `kind` argument)
    2. `type:` in the JSON body or the front matter
    3. the filename (SKILL.md, *.agent.md, *.memory.md, *.prompt.md, or a
       prompts/ skills/ memory/ agents/ path)
    4. the shape (goal/harness ⇒ agent, text ⇒ prompt, content ⇒ memory)
    5. prompt — the plainest thing a markdown file can be

docs/uploads.md is the human version of this file and ships with the module;
GET /library/formats serves it to the console.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KINDS = ("prompt", "skill", "memory", "agent")

MAX_UPLOAD_CHARS = 200_000

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "uploads.md"

# filename → kind, checked in order against the lowercased path
NAME_HINTS: List[Tuple[str, str]] = [
    ("skill.md", "skill"), (".skill.md", "skill"), ("/skills/", "skill"),
    (".agent.md", "agent"), ("agent.md", "agent"), ("/agents/", "agent"),
    (".memory.md", "memory"), ("memory.md", "memory"), ("/memory/", "memory"),
    (".note.md", "memory"), ("notes.md", "memory"),
    (".prompt.md", "prompt"), ("/prompts/", "prompt"),
]

# front-matter keys that only one kind ever carries
SHAPE_HINTS: List[Tuple[Tuple[str, ...], str]] = [
    (("goal", "harness", "icon", "system_prompt"), "agent"),
    (("allowed-tools", "allowed_tools", "when_to_use", "license"), "skill"),
]


# ── front matter ────────────────────────────────────────────────────

_FM = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split `---\\nkey: value\\n---\\nbody` into (meta, body)."""
    match = _FM.match(text or "")
    if not match:
        return {}, (text or "").strip()
    return _load_meta(match.group(1)), text[match.end():].strip()


def _load_meta(block: str) -> Dict[str, Any]:
    """PyYAML when it's installed, a flat key/value reader when it isn't —
    front matter in these formats is never deeper than a list of strings."""
    try:
        import yaml
        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _mini_yaml(block)


def _mini_yaml(block: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    key = None
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(_scalar(line[2:]))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        meta[key] = _scalar(value.strip()) if value.strip() else []
    return meta


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return None
    return value


# ── field coercion ──────────────────────────────────────────────────

def _text(value: Any) -> str:
    if value is None or isinstance(value, (list, dict)) and not value:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value).strip()


def _list(value: Any) -> Optional[List[str]]:
    """A list of names from a list, or from a comma/space separated string."""
    if value is None or value == []:
        return None
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    else:
        items = [v.strip() for v in re.split(r"[,\n]", str(value))]
    items = [i for i in items if i]
    return items or None


def slug(name: str) -> str:
    """The agent-registry form of a name: lowercase, dashes, no spaces."""
    return re.sub(r"[^a-z0-9._-]", "", (name or "").strip().lower()
                  .replace(" ", "-").replace("_", "-"))


def _stem(filename: Optional[str]) -> str:
    """A display name from a path — 'skills/pdf/SKILL.md' ⇒ 'pdf'."""
    if not filename:
        return ""
    path = Path(str(filename))
    stem = path.stem
    # SKILL.md / AGENT.md carry the name in the folder, not the file
    if stem.lower() in ("skill", "agent", "index", "readme", "prompt", "memory"):
        stem = path.parent.name or stem
    for suffix in (".agent", ".skill", ".prompt", ".memory", ".note"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem.replace("_", " ").replace("-", " ").strip() or path.stem


# ── kind detection ──────────────────────────────────────────────────

def detect(meta: Dict[str, Any], filename: Optional[str] = None,
           kind: Optional[str] = None) -> str:
    """Resolve the kind of an upload. See the module docstring for the order."""
    if kind and kind != "auto":
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind} (have: {', '.join(KINDS)})")
        return kind

    declared = _text(meta.get("type") or meta.get("kind")).lower()
    if declared in KINDS:
        return declared

    path = (filename or "").lower().replace("\\", "/")
    for needle, hinted in NAME_HINTS:
        if path.endswith(needle) or needle in path:
            return hinted

    for keys, hinted in SHAPE_HINTS:
        if any(k in meta for k in keys):
            return hinted
    if "text" in meta:
        return "prompt"
    if "content" in meta:
        return "memory"
    return "prompt"


# ── the parser ──────────────────────────────────────────────────────

def parse(text: str, filename: Optional[str] = None,
          kind: Optional[str] = None) -> Dict[str, Any]:
    """Normalize one uploaded file into a library item.

    Returns a dict with `kind` plus the fields that kind needs — the caller
    hands them to prompt_add / note_add / skill_add / agents.create.
    """
    if not (text or "").strip():
        raise ValueError("nothing to upload — the file is empty")

    body_text = text.strip()
    meta: Dict[str, Any] = {}
    body = body_text

    if body_text[0] in "{[":
        try:
            data = json.loads(body_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"looks like JSON but won't parse: {e}")
        if not isinstance(data, dict):
            raise ValueError("JSON upload must be a single object")
        meta = data
        # the body lives in whichever field this kind uses
        body = _text(data.get("body") or data.get("text") or data.get("content")
                     or data.get("goal") or data.get("prompt"))
    else:
        meta, body = split_frontmatter(body_text)
        if not body:
            # front matter only — the content field carries it instead
            body = _text(meta.get("body") or meta.get("text")
                         or meta.get("content") or meta.get("goal"))

    resolved = detect(meta, filename, kind)
    name = _text(meta.get("name") or meta.get("title")) or _stem(filename)
    if not name:
        raise ValueError("no name — add `name:` to the front matter or name the file")
    description = _text(meta.get("description") or meta.get("summary"))
    tags = _list(meta.get("tags") or meta.get("topics")) or []

    if resolved == "prompt":
        if not body:
            raise ValueError("a prompt needs its text")
        return {"kind": "prompt", "name": name, "description": description,
                "body": body, "tags": tags}

    if resolved == "memory":
        if not body:
            raise ValueError("a memory note needs its content")
        return {"kind": "memory", "name": name, "description": description,
                "body": body, "tags": tags}

    if resolved == "skill":
        if not body:
            raise ValueError("a skill needs its instructions")
        return {"kind": "skill", "name": name, "description": description,
                "body": body, "tags": tags,
                "source": _text(meta.get("source")) or "upload",
                "url": _text(meta.get("url") or meta.get("homepage")),
                "license": _text(meta.get("license")) or None}

    goal = _text(meta.get("goal") or meta.get("system_prompt") or meta.get("prompt")) or body
    if not goal:
        raise ValueError("an agent needs a goal — the body of the file is its "
                         "system prompt, or set `goal:` in the front matter")
    return {"kind": "agent", "name": slug(name), "label": name,
            "description": description, "body": goal,
            "icon": _text(meta.get("icon")) or ">_",
            "skills": _list(meta.get("skills") or meta.get("tools")),
            "model": _text(meta.get("model")) or None,
            "harness": _text(meta.get("harness")) or None,
            "tags": tags}


# ── the docs the console renders ────────────────────────────────────

def doc() -> str:
    """docs/uploads.md — the format reference shipped with the module."""
    try:
        return DOC_PATH.read_text()
    except OSError:
        return "# Uploads\n\ndocs/uploads.md is missing from this install.\n"


def spec() -> Dict[str, Any]:
    """Machine-readable summary of what upload accepts."""
    return {
        "kinds": list(KINDS),
        "carriers": ["json", "markdown+frontmatter", "plain text"],
        "detect": ["explicit kind", "type: field", "filename", "shape", "prompt"],
        "filenames": {needle: hinted for needle, hinted in NAME_HINTS},
        "max_chars": MAX_UPLOAD_CHARS,
        "doc": doc(),
    }
