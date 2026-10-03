"""promptland harvest — pull prompts from public collections on the internet.

Local-first and stdlib-only (urllib, csv, json): no scraping SaaS, no API
keys. A *source* is a small dict describing where prompts live and which
adapter reads them; adapters are pure generators, so adding a new source
kind is one function. Everything fetched lands in a local catalog under
~/.mod/promptland/harvest/, deduped by sha256 of the normalized body —
re-running a harvest only ever adds what's new.

Source kinds:
  csv          one prompt per row (name_col/body_col, defaults act/prompt)
  json         a JSON list of objects (name_key/body_key)
  github_tree  walk a public repo via the git trees API, keep files whose
               path matches `include` globs (minus `exclude`), one prompt
               per file — raw files come from raw.githubusercontent.com
  url          a single raw text/markdown document → one prompt

The default sources are well-known open-licensed collections (CC0 / MIT);
each item records its origin URL and source license for attribution.
"""

import csv
import fnmatch
import hashlib
import io
import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional

STATE = Path.home() / ".mod" / "promptland"
HARVEST_DIR = STATE / "harvest"
ITEMS_DIR = HARVEST_DIR / "items"
INDEX_FILE = HARVEST_DIR / "index.json"
SOURCES_FILE = HARVEST_DIR / "sources.json"

UA = "promptland-harvest/0.1 (mod protocol; local-first)"
TIMEOUT = 20
MAX_BODY = 200_000        # skip single prompts bigger than this
MIN_BODY = 40             # skip trivial fragments
MAX_FILES_PER_SOURCE = 500
MAX_ITEMS_PER_RUN = 2000
PREVIEW_LEN = 240

DEFAULT_SOURCES: List[dict] = [
    {
        "name": "awesome-chatgpt-prompts",
        "kind": "csv",
        "url": "https://raw.githubusercontent.com/f/awesome-chatgpt-prompts/main/prompts.csv",
        "license": "CC0-1.0",
        "tags": ["awesome", "role"],
    },
    {
        "name": "fabric-patterns",
        "kind": "github_tree",
        "repo": "danielmiessler/fabric",
        "branch": "main",
        # patterns moved from /patterns to /data/patterns — accept both
        "include": ["data/patterns/*/system.md", "patterns/*/system.md"],
        "license": "MIT",
        "tags": ["fabric", "pattern"],
    },
    {
        "name": "llm-prompt-library",
        "kind": "github_tree",
        "repo": "abilzerian/LLM-Prompt-Library",
        "branch": "main",
        "include": ["*.md"],
        "exclude": ["README*", "*/README*", "LICENSE*", ".github/*",
                    "CONTRIBUTING*", "SECURITY*", "CODE_OF_CONDUCT*"],
        "license": "MIT",
        "tags": ["library"],
    },
    {
        "name": "chatgpt-system-prompts",
        "kind": "github_tree",
        "repo": "mustvlad/ChatGPT-System-Prompts",
        "branch": "main",
        "include": ["prompts/*.md"],
        "license": "MIT",
        "tags": ["system"],
    },
]

_lock = threading.Lock()
_run_lock = threading.Lock()
STATUS: Dict = {"running": False}


def _init():
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCES_FILE.exists():
        SOURCES_FILE.write_text(json.dumps(DEFAULT_SOURCES, indent=2))


_init()


# ── catalog (index + item files) ─────────────────────────────────────────────

def _read_index() -> List[dict]:
    if INDEX_FILE.exists():
        try:
            data = json.loads(INDEX_FILE.read_text())
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _write_index(entries: List[dict]):
    INDEX_FILE.write_text(json.dumps(entries, indent=2))


def _hid(body: str) -> str:
    norm = re.sub(r"\s+", " ", body).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def get_item(hid: str) -> Optional[dict]:
    if not re.fullmatch(r"[0-9a-f]{16}", hid or ""):
        return None
    p = ITEMS_DIR / f"{hid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def delete_item(hid: str) -> bool:
    item = get_item(hid)
    if not item:
        return False
    (ITEMS_DIR / f"{hid}.json").unlink(missing_ok=True)
    with _lock:
        _write_index([e for e in _read_index() if e.get("hid") != hid])
    return True


def list_items(q: str = "", source: str = "", offset: int = 0, limit: int = 100) -> dict:
    entries = _read_index()
    if source:
        entries = [e for e in entries if e.get("source") == source]
    if q:
        needle = q.lower()
        entries = [e for e in entries if needle in (
            e.get("name", "") + " " + e.get("preview", "") + " " + " ".join(e.get("tags", []))
        ).lower()]
    total = len(entries)
    offset = max(0, int(offset))
    limit = max(1, min(500, int(limit)))
    return {"total": total, "offset": offset, "items": entries[offset:offset + limit]}


def stats() -> dict:
    entries = _read_index()
    by_source: Dict[str, int] = {}
    for e in entries:
        by_source[e.get("source", "?")] = by_source.get(e.get("source", "?"), 0) + 1
    return {"total": len(entries), "by_source": by_source}


# ── sources ──────────────────────────────────────────────────────────────────

def sources() -> List[dict]:
    try:
        data = json.loads(SOURCES_FILE.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return list(DEFAULT_SOURCES)


def add_source(src: dict) -> dict:
    name = re.sub(r"[^a-z0-9-]", "-", str(src.get("name", "")).strip().lower())[:48]
    kind = src.get("kind")
    if not name:
        raise ValueError("source needs a name")
    if kind not in ("csv", "json", "github_tree", "url"):
        raise ValueError("kind must be one of csv|json|github_tree|url")
    if kind == "github_tree":
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(src.get("repo", ""))):
            raise ValueError("github_tree needs repo as owner/name")
    else:
        url = str(src.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must be http(s)")
    clean = {k: src[k] for k in
             ("kind", "url", "repo", "branch", "include", "exclude",
              "name_col", "body_col", "name_key", "body_key", "license", "tags")
             if k in src}
    clean["name"] = name
    with _lock:
        entries = [s for s in sources() if s.get("name") != name]
        entries.append(clean)
        SOURCES_FILE.write_text(json.dumps(entries, indent=2))
    return clean


def remove_source(name: str) -> bool:
    with _lock:
        entries = sources()
        kept = [s for s in entries if s.get("name") != name]
        if len(kept) == len(entries):
            return False
        SOURCES_FILE.write_text(json.dumps(kept, indent=2))
    return True


# ── fetch adapters ───────────────────────────────────────────────────────────

def _http_get(url: str, max_bytes: int = 8_000_000) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing non-http url: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(max_bytes)


def _name_from_path(path: str) -> str:
    parts = path.split("/")
    base = re.sub(r"\.(md|txt|markdown)$", "", parts[-1], flags=re.I)
    # boilerplate filenames (fabric's system.md etc.) — name after the folder
    if base.lower() in ("system", "prompt", "index", "user") and len(parts) > 1:
        base = parts[-2]
    return re.sub(r"[-_]+", " ", base).strip() or path


def _iter_csv(src: dict) -> Iterable[dict]:
    text = _http_get(src["url"]).decode("utf-8", "replace")
    csv.field_size_limit(2_000_000)  # default 128KB aborts on giant rows
    reader = csv.DictReader(io.StringIO(text))
    name_col = src.get("name_col") or "act"
    body_col = src.get("body_col") or "prompt"
    for row in reader:
        name, body = row.get(name_col), row.get(body_col)
        if not (name and body):  # fall back to first two columns
            vals = list(row.values())
            if len(vals) >= 2:
                name, body = vals[0], vals[1]
        if name and body:
            yield {"name": name.strip(), "body": body.strip(), "origin": src["url"]}


def _iter_json(src: dict) -> Iterable[dict]:
    data = json.loads(_http_get(src["url"]).decode("utf-8", "replace"))
    if isinstance(data, dict):  # tolerate {items:[...]} / {prompts:[...]}
        data = data.get("items") or data.get("prompts") or []
    name_key = src.get("name_key") or "name"
    body_key = src.get("body_key") or "prompt"
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get(name_key) or row.get("act") or row.get("title")
        body = row.get(body_key) or row.get("body") or row.get("text")
        if name and body:
            yield {"name": str(name).strip(), "body": str(body).strip(), "origin": src["url"]}


def _iter_github_tree(src: dict) -> Iterable[dict]:
    repo, branch = src["repo"], src.get("branch", "main")
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    tree = json.loads(_http_get(tree_url).decode("utf-8", "replace")).get("tree", [])
    include = src.get("include") or ["*.md"]
    exclude = src.get("exclude") or []
    paths = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        if any(fnmatch.fnmatch(path, g) for g in include) and \
           not any(fnmatch.fnmatch(path, g) for g in exclude):
            paths.append(path)
    for path in paths[:MAX_FILES_PER_SOURCE]:
        raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        try:
            body = _http_get(raw, max_bytes=MAX_BODY + 1).decode("utf-8", "replace").strip()
        except Exception:
            continue
        yield {"name": _name_from_path(path), "body": body, "origin": raw}


def _iter_url(src: dict) -> Iterable[dict]:
    body = _http_get(src["url"], max_bytes=MAX_BODY + 1).decode("utf-8", "replace").strip()
    name = src.get("title") or _name_from_path(src["url"].split("?")[0])
    yield {"name": name, "body": body, "origin": src["url"]}


ADAPTERS = {"csv": _iter_csv, "json": _iter_json,
            "github_tree": _iter_github_tree, "url": _iter_url}


# ── harvest run ──────────────────────────────────────────────────────────────

def run_harvest(names: Optional[List[str]] = None, limit: int = MAX_ITEMS_PER_RUN) -> dict:
    """Fetch every (or the named) source into the catalog. Synchronous;
    the API wraps this in a thread and polls STATUS."""
    if not _run_lock.acquire(blocking=False):
        return {"error": "harvest already running", "status": STATUS}
    try:
        picked = [s for s in sources() if not names or s.get("name") in names]
        STATUS.update({
            "running": True, "started": int(time.time()), "finished": None,
            "sources_total": len(picked), "sources_done": 0, "source": None,
            "fetched": 0, "new": 0, "skipped": 0, "errors": [],
        })
        index = {e["hid"]: e for e in _read_index()}
        added = 0
        for src in picked:
            STATUS["source"] = src.get("name")
            adapter = ADAPTERS.get(src.get("kind", ""))
            if not adapter:
                STATUS["errors"].append(f"{src.get('name')}: unknown kind {src.get('kind')}")
                STATUS["sources_done"] += 1
                continue
            try:
                for item in adapter(src):
                    if added >= limit:
                        break
                    STATUS["fetched"] += 1
                    body = item["body"]
                    if not (MIN_BODY <= len(body) <= MAX_BODY):
                        STATUS["skipped"] += 1
                        continue
                    hid = _hid(body)
                    if hid in index:
                        STATUS["skipped"] += 1
                        continue
                    tags = list(dict.fromkeys((src.get("tags") or []) + [src["name"]]))[:12]
                    record = {
                        "hid": hid,
                        "name": item["name"][:120],
                        "body": body,
                        "tags": tags,
                        "source": src["name"],
                        "license": src.get("license"),
                        "origin": item.get("origin"),
                        "fetched_at": int(time.time()),
                    }
                    (ITEMS_DIR / f"{hid}.json").write_text(json.dumps(record, indent=2))
                    index[hid] = {
                        "hid": hid, "name": record["name"], "tags": tags,
                        "source": src["name"], "license": record["license"],
                        "origin": record["origin"], "len": len(body),
                        "preview": re.sub(r"\s+", " ", body)[:PREVIEW_LEN],
                        "fetched_at": record["fetched_at"],
                    }
                    added += 1
                    STATUS["new"] = added
            except Exception as e:
                STATUS["errors"].append(f"{src.get('name')}: {e}")
            STATUS["sources_done"] += 1
            with _lock:  # persist progress after each source, not just at the end
                _write_index(sorted(index.values(), key=lambda x: (x["source"], x["name"])))
        STATUS.update({"running": False, "finished": int(time.time()), "source": None})
        return {"new": added, "status": dict(STATUS)}
    finally:
        STATUS["running"] = False
        _run_lock.release()
