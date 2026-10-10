#!/usr/bin/env python3
"""Hourly hub sweep: search every hub type, cache the code of the MCP servers.

Runs from crontab once an hour. Two jobs, both idempotent:

1. SEARCH — hit the hub's /catalog (all registries: featured, index, peers,
   directories) with the broad empty query plus one rotating topic keyword per
   hour, and snapshot the results under ~/.mod/mcp/cache/search/. The console
   and any client get warm, recent search results; the rows found feed job 2.

2. CODE — cache the source of every server the hub can name:
   - fleet/sweep servers are local mods: rsync their tree (sources only, heavy
     build dirs excluded) into cache/code/local/<id>/ every run;
   - user servers and search rows that point at a GitHub repo (repo/homepage):
     shallow HEAD tarball via codeload, extracted into
     cache/code/github/<owner>__<repo>/, refreshed when older than a day,
     at most MAX_FETCHES new repos per run so one hour's cron stays polite.

   cache/code/index.json is the manifest: one entry per cached server with
   origin, path, bytes, file count and fetch time.

State stays off-tree in ~/.mod/mcp (the hub's own convention). Env knobs:
MCP_API_URL, MCP_HUB_DIR, MCP_CODE_REFRESH_SECS, MCP_CODE_MAX_FETCHES.
Extra search topics: ~/.mod/mcp/cron.json {"queries": ["..."]}.
"""

import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = os.environ.get("MCP_API_URL", "http://localhost:50360")
HUB_DIR = os.environ.get("MCP_HUB_DIR", os.path.expanduser("~/.mod/mcp"))
MOD_ROOT = "/root/mod/mod"

SEARCH_DIR = os.path.join(HUB_DIR, "cache", "search")
CODE_DIR = os.path.join(HUB_DIR, "cache", "code")
INDEX = os.path.join(CODE_DIR, "index.json")
LOCK = os.path.join(HUB_DIR, "cron_cache.lock")

REFRESH_SECS = int(os.environ.get("MCP_CODE_REFRESH_SECS", 86400))  # re-pull repos daily
MAX_FETCHES = int(os.environ.get("MCP_CODE_MAX_FETCHES", 10))       # new repos per run
TARBALL_CAP = 30 * 1024 * 1024   # refuse repo tarballs beyond this
FILE_CAP = 512 * 1024            # skip single files beyond this on extract
SEARCH_KEEP = 72                 # hourly search snapshots retained

# One of these rides along with the empty query each hour, so a day of runs
# covers the space without hammering the directories.
TOPICS = [
    "github", "search", "browser", "database", "postgres", "memory",
    "filesystem", "slack", "notion", "weather", "email", "calendar",
    "kubernetes", "docker", "aws", "crypto", "ethereum", "bitcoin",
    "scraping", "pdf", "sqlite", "youtube", "maps", "news",
]

RSYNC_EXCLUDES = [
    ".git", "node_modules", "target", ".next", "__pycache__", "dist",
    "build", ".venv", "venv", "*.pyc", "*.tsbuildinfo", ".cache",
]


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def api(path):
    with urllib.request.urlopen(API + path, timeout=60) as r:
        return json.loads(r.read().decode())


# ── job 1: search ────────────────────────────────────────────────────────────

def run_searches():
    queries = [""]
    queries.append(TOPICS[datetime.now(timezone.utc).hour % len(TOPICS)])
    try:
        with open(os.path.join(HUB_DIR, "cron.json")) as f:
            queries += json.load(f).get("queries", [])
    except (OSError, ValueError):
        pass

    os.makedirs(SEARCH_DIR, exist_ok=True)
    listings, snapshot = [], {"at": datetime.now(timezone.utc).isoformat(), "searches": []}
    for q in dict.fromkeys(queries):  # dedupe, keep order
        try:
            r = api(f"/catalog?q={urllib.parse.quote(q, safe='')}&registry=all&limit=50")
        except Exception as e:
            log(f"search {q!r}: FAILED {e}")
            continue
        rows = r.get("listings", [])
        log(f"search {q!r}: {len(rows)} rows from {','.join(r.get('sources', []))}")
        snapshot["searches"].append({"q": q, "count": len(rows), "listings": rows})
        listings += rows

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H")
    for name in (f"{stamp}.json", "latest.json"):
        with open(os.path.join(SEARCH_DIR, name), "w") as f:
            json.dump(snapshot, f, indent=1)
    old = sorted(f for f in os.listdir(SEARCH_DIR) if re.fullmatch(r"\d{8}-\d{2}\.json", f))
    for f in old[:-SEARCH_KEEP]:
        os.unlink(os.path.join(SEARCH_DIR, f))
    return listings


# ── job 2: code ──────────────────────────────────────────────────────────────

def load_index():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def dir_stats(path):
    files = total = 0
    for root, dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
                files += 1
            except OSError:
                pass
    return files, total


def find_mod_dir(mod_id):
    for tier in ("core", "orbit"):  # core wins name collisions, mirror that
        p = os.path.join(MOD_ROOT, tier, mod_id)
        if os.path.isdir(p):
            return p
    return None


def snapshot_local(server, index):
    mod_id = server["id"]
    src = find_mod_dir(mod_id)
    if not src:
        return
    dest = os.path.join(CODE_DIR, "local", mod_id)
    os.makedirs(dest, exist_ok=True)
    cmd = ["rsync", "-a", "--delete", f"--max-size={FILE_CAP}"]
    cmd += [f"--exclude={e}" for e in RSYNC_EXCLUDES]
    cmd += [src + "/", dest + "/"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        log(f"local {mod_id}: rsync failed: {r.stderr.strip()[:200]}")
        return
    files, size = dir_stats(dest)
    index[f"local:{mod_id}"] = {
        "id": mod_id, "kind": "local", "origin": src, "path": dest,
        "files": files, "bytes": size, "fetched_at": int(time.time()),
    }


GITHUB_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?].*)?$")


def github_repo(row):
    for field in ("repo", "homepage", "url"):
        m = GITHUB_RE.match(str(row.get(field) or ""))
        if m and m.group(1) not in ("features", "topics", "orgs"):
            return m.group(1), m.group(2)
    return None


def fetch_repo(owner, repo, index):
    key = f"github:{owner}/{repo}"
    dest = os.path.join(CODE_DIR, "github", f"{owner}__{repo}")
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD"
    req = urllib.request.Request(url, headers={"User-Agent": "mod-mcp-hub-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(TARBALL_CAP + 1)
    if len(data) > TARBALL_CAP:
        raise ValueError(f"tarball exceeds {TARBALL_CAP} bytes")

    tmp = dest + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    skip = {"node_modules", ".git", "dist", "build", "__pycache__", ".."}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar:
            parts = m.name.split("/", 1)  # strip the HEAD-<sha> top dir
            if len(parts) < 2 or not m.isfile() or m.size > FILE_CAP:
                continue
            rel = parts[1]
            if skip & set(rel.split("/")):
                continue
            out = os.path.join(tmp, rel)
            if not os.path.realpath(out).startswith(os.path.realpath(tmp)):
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with tar.extractfile(m) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)

    files, size = dir_stats(dest)
    index[key] = {
        "id": f"{owner}/{repo}", "kind": "github",
        "origin": f"https://github.com/{owner}/{repo}", "path": dest,
        "files": files, "bytes": size, "fetched_at": int(time.time()),
    }
    return files, size


def cache_code(listings):
    os.makedirs(CODE_DIR, exist_ok=True)
    index = load_index()

    try:
        servers = api("/servers")["servers"]
    except Exception as e:
        log(f"servers: FAILED {e}")
        servers = []

    for s in servers:
        if s.get("source") in ("fleet", "sweep"):
            try:
                snapshot_local(s, index)
            except Exception as e:
                log(f"local {s['id']}: {e}")

    # remote repos: registered user servers first, then this run's search rows
    seen, queue = set(), []
    for row in [s for s in servers if s.get("source") == "user"] + listings:
        gh = github_repo(row)
        if gh and gh not in seen:
            seen.add(gh)
            queue.append(gh)

    now, fetched = time.time(), 0
    for owner, repo in queue:
        if fetched >= MAX_FETCHES:
            break
        entry = index.get(f"github:{owner}/{repo}")
        if entry and now - entry.get("fetched_at", 0) < REFRESH_SECS \
                and ("error" in entry or os.path.isdir(entry.get("path", ""))):
            continue
        try:
            files, size = fetch_repo(owner, repo, index)
            log(f"github {owner}/{repo}: cached {files} files, {size} bytes")
            fetched += 1
        except Exception as e:
            log(f"github {owner}/{repo}: {e}")
            # remember the miss so a dead repo does not eat the budget hourly
            index[f"github:{owner}/{repo}"] = {
                "id": f"{owner}/{repo}", "kind": "github", "error": str(e)[:200],
                "origin": f"https://github.com/{owner}/{repo}", "fetched_at": int(time.time()),
            }
            fetched += 1

    with open(INDEX, "w") as f:
        json.dump(index, f, indent=1)
    ok = [e for e in index.values() if "error" not in e]
    log(f"index: {len(ok)} cached ({sum(e.get('bytes', 0) for e in ok)} bytes), "
        f"{len(index) - len(ok)} unreachable")


def main():
    os.makedirs(HUB_DIR, exist_ok=True)
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("previous run still going; skipping")
            return 0
        log(f"run start (api={API})")
        listings = run_searches()
        cache_code(listings)
        log("run done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
