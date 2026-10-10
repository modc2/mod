#!/usr/bin/env python3
"""hub api — the module catalog as a service (:50520, loopback only).

The HUB data plane, copied out of orbit/build per docs/hub-extraction.md
(phase 1). Key-for-key ports of:

    GET /modules                    ← build api.rs list_modules
    GET /modules/{name}/screenshot  ← build screenshots.rs
    GET /probe?ports=               ← build app /api/service GET (batch probe)
    autosnap loop + /autosnap/*     ← build autosnap.rs

plus the catalog fns hub already had (/modules/{name}/doc, /search).

Raw on-disk catalog by design: no privacy overlay, no owner attribution —
build applies its visibility overlay on the way out. That is also why this
service is NOT routed publicly ("route": false) and binds 127.0.0.1. The one
place privacy IS honored is autosnap: a module with an enabled record under
~/.mod/build/private/ never auto-registers (the api/reg push would publish
the plaintext tree). That read is file-only — hub never calls into build.

Run:  bash start.sh [port]      (uvicorn api:app, default 50520)
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

HOME = os.environ.get("HOME", "/tmp")
PORT = 50520
HERE = os.path.dirname(os.path.abspath(__file__))

# Dirs newest_mtime() will not descend into — same list as build api.rs.
MTIME_SKIP = {"node_modules", "target", "dist", "build", "out", "__pycache__", "venv", "logs"}
# find_nested_mods() adds two more.
NESTED_SKIP = MTIME_SKIP | {"cache", "artifacts"}
# Dirs a snapshot's byte-count skips — same list as build snapshots.rs.
SNAP_SKIP = {"node_modules", "target", "target-docker", "vendor", "__pycache__",
             ".git", ".next", "dist", "build", ".venv", "venv", "blobs"}

app = FastAPI(title="hub", docs_url=None, redoc_url=None)


# ── anchor / tree helpers ────────────────────────────────────────────────

def module_anchor_root() -> Path:
    return Path(HOME) / "mod"


def safe_anchor(raw) -> str:
    """The anchor a caller may scan: their hint when it stays inside the
    module tree, else the tree root itself."""
    root = module_anchor_root()
    root_real = root.resolve() if root.exists() else root
    if raw:
        p = Path(os.path.expanduser(str(raw)))
        try:
            real = p.resolve()
        except OSError:
            real = p
        if real == root_real or str(real).startswith(str(root_real) + os.sep):
            return str(real)
    return str(root_real)


def display(path: str) -> str:
    return path.replace(HOME, "~", 1) if path.startswith(HOME) else path


def newest_mtime(dir_path: str, depth: int, newest: int) -> int:
    """Newest file mtime under a module dir — depth-capped, skip list as build."""
    if depth == 0:
        return newest
    try:
        entries = list(os.scandir(dir_path))
    except OSError:
        return newest
    for entry in entries:
        name = entry.name
        try:
            if entry.is_dir(follow_symlinks=False):
                if name.startswith(".") or name in MTIME_SKIP:
                    continue
                newest = newest_mtime(entry.path, depth - 1, newest)
            else:
                secs = int(entry.stat(follow_symlinks=False).st_mtime)
                if secs > newest:
                    newest = secs
        except OSError:
            continue
    return newest


# Birth times never change — cache them so /modules doesn't shell out on
# every call. Linux python has no statx birth-time API; `stat -c %W` does.
_BIRTH: dict = {}


def created_at(path: str):
    if path in _BIRTH:
        return _BIRTH[path]
    try:
        out = subprocess.run(["stat", "-c", "%W", path], capture_output=True,
                             text=True, timeout=5)
        w = int(out.stdout.strip() or "0")
        val = w if w > 0 else None
    except Exception:
        val = None
    _BIRTH[path] = val
    return val


def read_config(*paths):
    for p in paths:
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def registry_map():
    """~/.mod/api/registry.json — `{owner: {mod: cid}}` at the root; older
    writes wrapped the same map in `data`. Accept both."""
    data = read_config(os.path.join(HOME, ".mod", "api", "registry.json"))
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


def registry_cid(reg, name: str):
    if not reg:
        return None
    lower = name.lower()
    # Sorted owner order — build's serde_json maps iterate keys sorted, so
    # when a module has CIDs under several owners the same one must win here.
    for _, owner_mods in sorted(reg.items()):
        if isinstance(owner_mods, dict):
            cid = owner_mods.get(lower) or owner_mods.get(name)
            if isinstance(cid, str):
                return cid
    return None


def str_list(config, key):
    v = (config or {}).get(key)
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def find_nested_mods(root: str, dir_path: str, depth: int, out: list):
    """Subdirectories of a module that are mods in their own right
    (own mod.py or config.json, addressable as `m {module}/{rel}`).
    */src is the module itself — walked transparently, segment elided."""
    if depth == 0:
        return
    try:
        entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        name = entry.name
        if name.startswith((".", "_")) or name in NESTED_SKIP:
            continue
        path = entry.path
        if name == "src":
            find_nested_mods(root, path, depth - 1, out)
            continue
        has_mod_py = (os.path.isfile(os.path.join(path, "mod.py"))
                      or os.path.isfile(os.path.join(path, "src", "mod.py")))
        config_path = os.path.join(path, "config.json")
        if not os.path.isfile(config_path):
            config_path = os.path.join(path, "src", "config.json")
        has_config = os.path.isfile(config_path)
        if has_mod_py or has_config:
            rel = "/".join(c for c in os.path.relpath(path, root).split(os.sep)
                           if c != "src")
            if any(m["rel"] == rel for m in out):
                find_nested_mods(root, path, depth - 1, out)
                continue
            config = read_config(config_path) if has_config else None
            urls = (config or {}).get("urls") or {}
            out.append({
                "rel": rel,
                "name": (config or {}).get("name") or name,
                "description": (config or {}).get("description"),
                "has_config": has_config,
                "has_mod_py": has_mod_py,
                "path": path,
                "app_url": urls.get("app") or (config or {}).get("app_url"),
                "api_url": urls.get("api") or (config or {}).get("api_url"),
                "version": (config or {}).get("version"),
                "fns": str_list(config, "fns"),
            })
        # A nested mod can itself contain mods (agent/src → agent/src/skills/*).
        find_nested_mods(root, path, depth - 1, out)


# ── the catalog scan — build api.rs list_modules, key-for-key ────────────

def scan_catalog(q: str = "", anchor_raw=None) -> dict:
    query = (q or "").lower()
    anchor = safe_anchor(anchor_raw)
    reg = registry_map()
    modules = []

    # The module tree root itself is selectable as the "mod" module; its
    # config.json lives one level above the tree ({anchor}/config.json).
    mod_root = os.path.join(anchor, "mod")
    if os.path.isdir(mod_root) and (not query or query in "mod"):
        root_config = read_config(os.path.join(anchor, "config.json")) or {}
        modules.append({
            "name": "mod",
            "path": mod_root,
            "display": display(mod_root),
            "category": "root",
            "has_config": True,
            "app_url": None,
            "api_url": None,
            "description": "The whole module tree — every mod under orbit/ and core/. Select it for cross-module work: one job can read and edit any module.",
            "fns": [],
            "has_app_dir": False,
            "has_server_dir": False,
            "has_api_dir": False,
            # Filled from the scan results after the loop — the walk isn't
            # done twice.
            "mods": [],
            "owner": None,
            "version": root_config.get("version"),
            "cid": None,
            "deps": [],
            "created_at": created_at(mod_root),
            "updated_at": newest_mtime(mod_root, 1, 0) or None,
        })

    scan_dirs = [(os.path.join(anchor, "mod", "orbit"), "orbit"),
                 (os.path.join(anchor, "mod", "core"), "core")]

    for scan_dir, category in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        # The tree roots are themselves mods: a config.json at orbit/ or
        # core/ names the tree. Dir hints are ignored here — at the root
        # those are sibling MODULES named "app"/"api".
        config = read_config(os.path.join(scan_dir, "config.json"))
        if config is not None:
            name = config.get("name") or category
            if not query or query in name.lower():
                urls = config.get("urls") or {}
                modules.append({
                    "name": name,
                    "path": scan_dir,
                    "display": display(scan_dir),
                    "category": category,
                    "has_config": True,
                    "app_url": urls.get("app") or config.get("app_url"),
                    "api_url": urls.get("api") or config.get("api_url"),
                    "description": config.get("description"),
                    "fns": str_list(config, "fns"),
                    "has_app_dir": False,
                    "has_server_dir": False,
                    "has_api_dir": False,
                    "mods": [],
                    "owner": config.get("owner"),
                    "version": config.get("version"),
                    "cid": registry_cid(reg, name),
                    "deps": str_list(config, "deps"),
                    "created_at": created_at(scan_dir),
                    "updated_at": newest_mtime(scan_dir, 1, 0) or None,
                })
        try:
            entries = list(os.scandir(scan_dir))
        except OSError:
            entries = []
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            if name.startswith((".", "_")):
                continue
            if query and query not in name.lower():
                continue
            path = entry.path

            # config.json may sit at the module root, under {name}/, or under
            # src/ (*/src IS */ — a config there names this module).
            config = read_config(os.path.join(path, "config.json"),
                                 os.path.join(path, name, "config.json"),
                                 os.path.join(path, "src", "config.json"))
            has_config = config is not None
            urls = (config or {}).get("urls") or {}

            has_mod_py = (os.path.isfile(os.path.join(path, "mod.py"))
                          or os.path.isfile(os.path.join(path, "src", "mod.py")))
            nested = []
            find_nested_mods(path, path, 4, nested)
            nested.sort(key=lambda m: m["rel"])

            # A directory is only a mod where a config.json or mod.py actually
            # is — its own, or one nested deeper. Marker-less dirs would
            # surface as phantom hub modules.
            if not has_config and not has_mod_py and not nested:
                continue

            modules.append({
                "name": name,
                "path": path,
                "display": display(path),
                "category": category,
                "has_config": has_config,
                "has_mod_py": has_mod_py,
                "app_url": urls.get("app") or (config or {}).get("app_url"),
                "api_url": urls.get("api") or (config or {}).get("api_url"),
                "description": (config or {}).get("description"),
                "fns": str_list(config, "fns"),
                # */src is transparent — src/app counts as the module's app.
                "has_app_dir": os.path.isdir(os.path.join(path, "app"))
                               or os.path.isdir(os.path.join(path, "src", "app")),
                "has_server_dir": os.path.isdir(os.path.join(path, "server"))
                                  or os.path.isdir(os.path.join(path, "src", "server")),
                "has_api_dir": os.path.isdir(os.path.join(path, "api"))
                               or os.path.isdir(os.path.join(path, "src", "api")),
                "mods": nested,
                "owner": (config or {}).get("owner"),
                "version": (config or {}).get("version"),
                "cid": registry_cid(reg, name),
                "deps": str_list(config, "deps"),
                "created_at": created_at(path),
                "updated_at": newest_mtime(path, 6, 0) or None,
            })

    # The tree roots skipped the nested-mods walk — their nested mods ARE the
    # fleet just scanned. Fill from the collected rows; these are top-level
    # modules, so the protocol reaches them as `m {name}`, not `m mod/{rel}`.
    orbit_root = os.path.join(anchor, "mod", "orbit")
    core_root = os.path.join(anchor, "mod", "core")
    root_mods = []
    tree_mods = {}
    for m in modules:
        if m["category"] not in ("orbit", "core"):
            continue
        path = m["path"]
        has_mod_py = (os.path.isfile(os.path.join(path, "mod.py"))
                      or os.path.isfile(os.path.join(path, "src", "mod.py")))
        if not m["has_config"] and not has_mod_py:
            continue
        dir_name = os.path.basename(path) or m["name"]
        def row(rel, m=m, has_mod_py=has_mod_py, path=path):
            return {
                "rel": rel,
                "name": m["name"],
                "description": m["description"],
                "has_config": m["has_config"],
                "has_mod_py": has_mod_py,
                "addr": f"m {m['name']}",
                "path": path,
                "app_url": m["app_url"],
                "api_url": m["api_url"],
            }
        if path in (orbit_root, core_root):
            root_mods.append(row(dir_name))
        else:
            root_mods.append(row(f"{m['category']}/{dir_name}"))
            tree_mods.setdefault(m["category"], []).append(row(dir_name))
    root_mods.sort(key=lambda r: r["rel"])
    for lst in tree_mods.values():
        lst.sort(key=lambda r: r["rel"])
    for m in modules:
        if m["category"] == "root":
            m["mods"] = root_mods
        elif m["path"] == orbit_root:
            m["mods"] = tree_mods.get("orbit", [])
        elif m["path"] == core_root:
            m["mods"] = tree_mods.get("core", [])

    modules.sort(key=lambda m: m["name"])
    return {"modules": modules, "count": len(modules), "anchor": display(anchor)}


# ── screenshots — build screenshots.rs, same policy constants ────────────

FRESH_TTL = 6 * 3600          # serve a cached shot without refreshing
FAIL_TTL = 10 * 60            # negative cache after a failed capture
CAPTURE_TIMEOUT = 45          # hard cap on one chrome run
REFRESH_FLOOR = 60            # ?refresh=1 ignored if shot younger than this
FRESH_FLOOR = 10              # ?fresh=1 sync-capture floor

_shot_locks: dict = {}
_chrome_slots = asyncio.Semaphore(2)


def shots_dir() -> Path:
    d = Path(HOME) / ".mod" / "hub" / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def gateway_host() -> str:
    """The router's public host — parsed from the generated caddy site block
    so it tracks `m caddy/host`. Env override wins."""
    for var in ("HUB_SHOT_HOST", "BUILD_SHOT_HOST"):
        h = os.environ.get(var, "").strip()
        if h:
            return h
    try:
        with open("/etc/caddy/mod_site.caddy") as f:
            for line in list(f)[:10]:
                idx = line.find("host=")
                if idx >= 0:
                    host = line[idx + 5:].split()[0] if line[idx + 5:].split() else ""
                    if host:
                        return host
    except OSError:
        pass
    return "modc2.com"


def browser_bin():
    """Locate a headless chromium — playwright installs first, PATH fallback."""
    for var in ("HUB_SHOT_BROWSER", "BUILD_SHOT_BROWSER"):
        p = os.environ.get(var, "")
        if p and os.path.isfile(p):
            return p
    pw = Path(HOME) / ".cache" / "ms-playwright"
    candidates = []
    try:
        for entry in os.scandir(pw):
            if entry.name.startswith("chromium_headless_shell-"):
                candidates.append(os.path.join(entry.path, "chrome-headless-shell-linux64/chrome-headless-shell"))
            elif entry.name.startswith("chromium-"):
                candidates.append(os.path.join(entry.path, "chrome-linux/chrome"))
    except OSError:
        pass
    candidates = [c for c in candidates if os.path.isfile(c)]
    candidates.sort(key=lambda s: ("headless_shell" in s, s))
    if candidates:
        return candidates[-1]
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        try:
            out = subprocess.run(["which", name], capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except OSError:
            continue
    return None


def valid_name(name: str) -> bool:
    return (bool(name) and len(name) <= 64
            and all(c.isalnum() or c in "-_" for c in name))


def module_has_app(name: str) -> bool:
    """Only modules that exist locally and ship an app get a capture."""
    for tree in ("orbit", "core"):
        d = os.path.join(HOME, "mod", "mod", tree, name)
        if not os.path.isdir(d):
            continue
        if os.path.isdir(os.path.join(d, "app")) or os.path.isdir(os.path.join(d, "src", "app")):
            return True
        for cfg in (os.path.join(d, "config.json"), os.path.join(d, name, "config.json")):
            v = read_config(cfg)
            if v and ((v.get("urls") or {}).get("app") or v.get("app_url")):
                return True
        # Dir exists but no app signal — a shot of the gateway 404 page is
        # worse than the letter-tile fallback.
        return False
    return False


def age_of(path: Path):
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def fail_marker(png: Path) -> Path:
    return png.with_suffix(".fail")


def shot_lock(name: str) -> asyncio.Lock:
    if name not in _shot_locks:
        _shot_locks[name] = asyncio.Lock()
    return _shot_locks[name]


async def capture(name: str, path: str, w: int, h: int, budget_ms: int, dest: Path) -> str | None:
    """One capture: pre-flight through the local caddy, then chrome renders
    and screenshots. Atomic write (tmp + rename). Returns an error string or
    None on success."""
    bin_ = browser_bin()
    if not bin_:
        return "no headless chromium available on this host"
    host = gateway_host()
    url = f"https://{host}/{name}{path}"

    # Pre-flight (also pokes the activator awake for sleeping modules).
    try:
        pre = await asyncio.create_subprocess_exec(
            "curl", "-ksSL", "-o", "/dev/null", "-w", "%{http_code}",
            "--resolve", f"{host}:443:127.0.0.1", "--max-time", "20", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await pre.communicate()
        code = int(out.strip() or "0")
    except (OSError, ValueError) as e:
        return f"pre-flight {url}: {e}"
    if not (200 <= code < 300):
        return f"{url} answered {code}"

    async with _chrome_slots:
        tmp = dest.with_suffix(".tmp.png")
        tmp.unlink(missing_ok=True)
        proc = await asyncio.create_subprocess_exec(
            bin_, "--headless", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--disable-extensions",
            "--force-color-profile=srgb",
            f"--window-size={w},{h}",
            f"--screenshot={tmp}",
            # Fast-forward timers/animations so SPAs settle without a wall wait.
            f"--virtual-time-budget={budget_ms}",
            f"--timeout={budget_ms + 15_000}",
            f"--host-resolver-rules=MAP {host} 127.0.0.1",
            "--ignore-certificate-errors", url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            rc = await asyncio.wait_for(proc.wait(), CAPTURE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            tmp.unlink(missing_ok=True)
            return "capture timed out"
        if rc != 0:
            tmp.unlink(missing_ok=True)
            return f"chrome exited with {rc}"
        try:
            if tmp.stat().st_size < 1024:
                tmp.unlink(missing_ok=True)
                return "capture produced no usable image"
            tmp.rename(dest)
        except OSError as e:
            return f"rename: {e}"
        fail_marker(dest).unlink(missing_ok=True)
        return None


async def capture_locked(name: str, png: Path, min_age: float) -> str | None:
    """Capture under the per-module lock, re-checking freshness after
    acquiring so a burst of hub loads collapses into one chrome run."""
    async with shot_lock(name):
        a = age_of(png)
        if a is not None and a < min_age:
            return None  # someone else just captured it
        err = await capture(name, "", 800, 500, 10_000, png)
        if err:
            try:
                fail_marker(png).write_text(err)
            except OSError:
                pass
        return err


def serve_png(png: Path):
    try:
        return Response(png.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=300"})
    except OSError:
        return Response("no screenshot", status_code=404)


@app.get("/modules/{name}/screenshot")
async def module_screenshot(name: str, refresh: str = "", fresh: str = ""):
    if not valid_name(name):
        return Response("bad module name", status_code=400)
    if not module_has_app(name):
        return Response("module has no app", status_code=404)

    png = shots_dir() / f"{name}.png"
    force = refresh in ("1", "true")
    want_fresh = fresh in ("1", "true")
    a = age_of(png)

    # ?fresh=1 — capture inline and only then answer. Failure still serves a
    # stale shot if any exists: an out-of-date attachment beats none.
    if want_fresh:
        err = await capture_locked(name, png, FRESH_FLOOR)
        if png.is_file():
            return serve_png(png)
        return Response(f"capture failed: {err or 'no screenshot'}", status_code=404)

    if a is not None and not force and a < FRESH_TTL:
        return serve_png(png)
    if a is not None:
        # Stale (or refresh requested): serve now, refresh in the background.
        # The floor keeps ?refresh from being a free chrome-spawning lever.
        min_age = REFRESH_FLOOR if force else FRESH_TTL
        if a >= min_age:
            asyncio.get_running_loop().create_task(capture_locked(name, png, min_age))
        return serve_png(png)
    # No shot yet: respect the negative cache, then capture inline so the very
    # first hub render still gets real pictures.
    fa = age_of(fail_marker(png))
    if fa is not None and fa < FAIL_TTL and not force:
        return Response("capture failed recently", status_code=404)
    err = await capture_locked(name, png, FRESH_TTL)
    if err:
        return Response(f"capture failed: {err}", status_code=404)
    return serve_png(png)


# ── autosnap — build autosnap.rs, same policy constants ──────────────────

TICK_SECS = 60
PER_TICK = 3                       # api/reg is ~15-20s per fresh snapshot
MAX_TREE_BYTES = 256 * 1024 * 1024
BACKOFF_BASE_SECS = 600            # doubles per consecutive failure
BACKOFF_MAX_SECS = 6 * 3600

_snap_status = {"last_tick": 0, "pending": 0, "snapped": 0, "failed": 0, "recent": []}
_snap_backoff: dict = {}           # name → (consecutive failures, next allowed ts)


def autosnap_enabled() -> bool:
    return os.environ.get("HUB_AUTOSNAP", "") != "0"


def api_module_url() -> str:
    return (os.environ.get("HUB_API_MODULE_URL")
            or os.environ.get("BUILD_API_MODULE_URL")
            or "http://127.0.0.1:8000")


def is_private(module: str) -> bool:
    """Read build's privacy records straight off disk (never call build):
    a module with an enabled record must not have its plaintext tree pushed."""
    safe = "".join(c for c in module if c.isalnum() or c in "-_/")
    rec = read_config(os.path.join(HOME, ".mod", "build", "private",
                                   safe.replace("/", "__") + ".json"))
    return bool(rec and rec.get("enabled"))


def dir_bytes(root: str) -> int:
    """Total content bytes a snapshot would read (same skip rules as build's
    snapshots.rs), so oversized trees are refused before any push."""
    total = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0
    for entry in entries:
        name = entry.name
        try:
            if entry.is_dir(follow_symlinks=False):
                if name in SNAP_SKIP or name.startswith("."):
                    continue
                total += dir_bytes(entry.path)
            elif entry.is_file(follow_symlinks=False) and not name.startswith("."):
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def snap_scan_modules():
    """Every module dir under the scan trees — same conventions as /modules.
    The tree roots themselves are left to manual snapshots (too big)."""
    out = []
    for tree in ("orbit", "core"):
        d = os.path.join(HOME, "mod", "mod", tree)
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            dir_name = entry.name
            if dir_name.startswith((".", "_")):
                continue
            path = entry.path
            has_config = os.path.isfile(os.path.join(path, "config.json"))
            has_mod_py = (os.path.isfile(os.path.join(path, "mod.py"))
                          or os.path.isfile(os.path.join(path, "src", "mod.py")))

            def has_nested():
                try:
                    return any(e.is_dir(follow_symlinks=False)
                               and not e.name.startswith((".", "_"))
                               and (os.path.isfile(os.path.join(e.path, "config.json"))
                                    or os.path.isfile(os.path.join(e.path, "mod.py")))
                               for e in os.scandir(path))
                except OSError:
                    return False
            if not has_config and not has_mod_py and not has_nested():
                continue
            config = read_config(os.path.join(path, "config.json")) or {}
            out.append((config.get("name") or dir_name, path))
    return out


def registered_names() -> set:
    """Lowercased module keys with a CID under ANY owner in the registry."""
    reg = registry_map() or {}
    out = set()
    for owner_mods in reg.values():
        if isinstance(owner_mods, dict):
            out.update(k.lower() for k in owner_mods)
    return out


def _post_reg(module: str) -> str:
    """POST api/reg — the registry CID is what makes the hub card show a cid.
    Blocking; run in an executor."""
    import urllib.request
    req = urllib.request.Request(
        api_module_url().rstrip("/") + "/api/reg",
        data=json.dumps({"mod": module, "comment": "autosnap: initial cid"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if isinstance(body.get("error"), str):
        raise RuntimeError(f"api/reg error: {body['error']}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("api/reg missing 'result'")
    return result.get("cid") or ""


async def snap_one(name: str, root: str) -> str:
    size = await asyncio.get_running_loop().run_in_executor(None, dir_bytes, root)
    if size > MAX_TREE_BYTES:
        raise RuntimeError(f"tree too large ({size} bytes)")
    return await asyncio.get_running_loop().run_in_executor(None, _post_reg, name)


async def autosnap_tick():
    registered = registered_names()
    now = int(time.time())
    queue = [(name, path) for name, path in snap_scan_modules()
             if name.lower() not in registered
             and os.path.basename(path).lower() not in registered
             # Private modules never auto-register.
             and not is_private(name)]
    queue.sort(key=lambda t: t[0])
    eligible = [(n, p) for n, p in queue
                if _snap_backoff.get(n, (0, 0))[1] <= now]
    _snap_status["last_tick"] = now
    _snap_status["pending"] = len(eligible)
    for name, root in eligible[:PER_TICK]:
        try:
            cid = await snap_one(name, root)
            _snap_backoff.pop(name, None)
            _snap_status["snapped"] += 1
            _snap_status["pending"] = max(0, _snap_status["pending"] - 1)
            _snap_status["recent"].append({"module": name, "cid": cid, "ts": int(time.time())})
        except Exception as e:
            fails = _snap_backoff.get(name, (0, 0))[0] + 1
            delay = min(BACKOFF_BASE_SECS << min(fails - 1, 6), BACKOFF_MAX_SECS)
            _snap_backoff[name] = (fails, int(time.time()) + delay)
            _snap_status["failed"] += 1
            _snap_status["recent"].append({"module": name, "error": str(e), "ts": int(time.time())})
        del _snap_status["recent"][:-20]


async def autosnap_loop():
    while True:
        await asyncio.sleep(TICK_SECS)
        try:
            await autosnap_tick()
        except Exception:
            pass


@app.on_event("startup")
async def _start_autosnap():
    if autosnap_enabled():
        asyncio.get_running_loop().create_task(autosnap_loop())


@app.get("/autosnap/status")
async def autosnap_status():
    return {"enabled": autosnap_enabled(), "tick_secs": TICK_SECS,
            "per_tick": PER_TICK, **{k: _snap_status[k] for k in
            ("last_tick", "pending", "snapped", "failed", "recent")}}


@app.post("/autosnap/tick")
async def autosnap_tick_now():
    await autosnap_tick()
    return await autosnap_status()


# ── plain catalog endpoints ──────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True, "name": "hub", "port": PORT}


@app.get("/config")
async def hub_config():
    return read_config(os.path.join(HERE, "config.json")) or {}


@app.get("/modules")
async def modules(q: str = "", anchor: str = ""):
    return await asyncio.get_running_loop().run_in_executor(
        None, scan_catalog, q, anchor or None)


@app.get("/modules/{name}")
async def module_one(name: str):
    catalog = await asyncio.get_running_loop().run_in_executor(
        None, scan_catalog, name, None)
    for m in catalog["modules"]:
        if m["name"] == name or os.path.basename(m["path"]) == name:
            config = read_config(os.path.join(m["path"], "config.json"),
                                 os.path.join(m["path"], name, "config.json"),
                                 os.path.join(m["path"], "src", "config.json"))
            return {**m, "config": config}
    return JSONResponse({"error": f"module '{name}' not found"}, status_code=404)


@app.get("/modules/{name}/doc")
async def module_doc(name: str):
    for tree in ("orbit", "core"):
        d = os.path.join(HOME, "mod", "mod", tree, name)
        if not os.path.isdir(d):
            continue

        def read(fn):
            p = os.path.join(d, fn)
            try:
                return open(p).read()
            except OSError:
                return None
        config = read_config(os.path.join(d, "config.json"),
                             os.path.join(d, name, "config.json"),
                             os.path.join(d, "src", "config.json")) or {}
        return {"module": name, "description": config.get("description", ""),
                "readme": read("README.md"), "skill": read("skill.md")}
    return JSONResponse({"error": f"module '{name}' not found"}, status_code=404)


@app.get("/search")
async def search(q: str = ""):
    query = q.lower()
    catalog = await asyncio.get_running_loop().run_in_executor(
        None, scan_catalog, "", None)
    results = [{"name": m["name"], "category": m["category"],
                "description": m["description"]}
               for m in catalog["modules"]
               if query in (m["name"] + " " + (m["description"] or "")).lower()]
    return {"ok": True, "count": len(results), "results": results}


# ── batch port probe — build app /api/service GET, batch form ────────────

MAX_BATCH_PORTS = 256


async def port_in_use(port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=1.0)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


@app.get("/probe")
async def probe(ports: str = ""):
    parsed = []
    for s in ports.split(","):
        s = s.strip()
        if not s:
            continue
        try:
            p = int(s)
        except ValueError:
            return JSONResponse({"ok": False, "error": "port must be an integer 1-65535"},
                                status_code=400)
        if not 1 <= p <= 65535:
            return JSONResponse({"ok": False, "error": "port must be an integer 1-65535"},
                                status_code=400)
        parsed.append(p)
    unique = list(dict.fromkeys(parsed))[:MAX_BATCH_PORTS]
    results = await asyncio.gather(*(port_in_use(p) for p in unique))
    return {"ok": True, "ports": dict(zip(unique, results))}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("HUB_PORT", PORT)))
