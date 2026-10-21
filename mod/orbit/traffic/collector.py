"""Tail Caddy's JSON access log into a small SQLite file of who used which app.

A "user" is one browser: a salted hash of the real client IP (Cloudflare's
Cf-Connecting-Ip, else the socket address) plus the User-Agent. The raw IP is
never written down. Every request is attributed to the app whose route it hit
(/<app>/..., /<app>/api/..., /api/<app>/...), so an open console that polls its
own API counts as someone using that app.

    seen(app, minute, vid, hits)   one row per app x minute x user
"""

import builtins
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time

LOG = os.environ.get("TRAFFIC_LOG", "/var/log/caddy/access.log")
DIR = os.path.expanduser(os.environ.get("TRAFFIC_DIR", "~/.mod/traffic"))
SITE = os.environ.get("TRAFFIC_SITE", "/etc/caddy/mod_site.caddy")
KEEP_DAYS = 35
LIVE_WINDOW = 300  # seconds: "currently using" = any request in the last 5 min

BOT = re.compile(
    r"bot|crawl|spider|slurp|curl|wget|python|httpx|aiohttp|go-http|okhttp|java/|"
    r"headless|lighthouse|monitor|uptime|preview|scan|fetch|axios|node-fetch|"
    r"facebookexternalhit|embedly|whatsapp|telegram|discord|slack", re.I)
ROUTE = re.compile(r"^\s*@([A-Za-z0-9_.-]+?)_(app|api|alias)\s+path\s")
ALIAS = re.compile(r"redir \* /([A-Za-z0-9_.-]+)\{uri\}")


def _hdr(headers, name):
    v = (headers or {}).get(name)
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else "")


def _internal(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_loopback or a.is_private
    except ValueError:
        return False


def load_routes(path=SITE):
    """App names the router serves, plus alias -> target."""
    apps, aliases, last = set(), {}, None
    try:
        with open(path) as f:
            for line in f:
                m = ROUTE.match(line)
                if m:
                    last = m.group(1) if m.group(2) == "alias" else None
                    if m.group(2) != "alias":
                        apps.add(m.group(1))
                    continue
                if last:
                    a = ALIAS.search(line)
                    if a:
                        aliases[last] = a.group(1)
                        last = None
    except OSError:
        pass
    return apps, aliases


def app_of(uri, apps, aliases, root="build"):
    path = uri.split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return root  # the bare host lands on /build
    first = parts[0]
    if first == "api" and len(parts) > 1:
        first = parts[1]
    first = aliases.get(first, first)
    return first if first in apps else None


class Store:
    def __init__(self, dir=DIR):
        os.makedirs(dir, exist_ok=True)
        self.dir = dir
        self.db = sqlite3.connect(os.path.join(dir, "traffic.db"), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS seen(app TEXT, minute INTEGER, vid TEXT,
                    hits INTEGER, PRIMARY KEY(app, minute, vid)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS seen_minute ON seen(minute);
                CREATE TABLE IF NOT EXISTS visitor(vid TEXT PRIMARY KEY, first INTEGER,
                    last INTEGER, country TEXT);
                CREATE TABLE IF NOT EXISTS cursor(path TEXT PRIMARY KEY, inode INTEGER,
                    offset INTEGER);
            """)
        salt_path = os.path.join(dir, "salt")
        if not os.path.exists(salt_path):
            with open(salt_path, "w") as f:
                f.write(secrets.token_hex(16))
            os.chmod(salt_path, 0o600)
        self.salt = open(salt_path).read().strip()

    def vid(self, ip, ua):
        return hashlib.sha256(f"{self.salt}|{ip}|{ua}".encode()).hexdigest()[:16]

    def add(self, rows, visitors):
        with self.lock:
            self.db.executemany(
                "INSERT INTO seen VALUES(?,?,?,?) ON CONFLICT(app,minute,vid) "
                "DO UPDATE SET hits=hits+excluded.hits", rows)
            self.db.executemany(
                "INSERT INTO visitor VALUES(?,?,?,?) ON CONFLICT(vid) DO UPDATE SET "
                "last=max(last,excluded.last), first=min(first,excluded.first)", visitors)
            self.db.commit()

    def cursor(self, path):
        r = self.db.execute("SELECT inode, offset FROM cursor WHERE path=?", (path,)).fetchone()
        return r or (None, 0)

    def set_cursor(self, path, inode, offset):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cursor VALUES(?,?,?)", (path, inode, offset))
            self.db.commit()

    def prune(self):
        cut = int(time.time() // 60) - KEEP_DAYS * 1440
        with self.lock:
            self.db.execute("DELETE FROM seen WHERE minute < ?", (cut,))
            self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()


class Collector:
    def __init__(self, store, log=LOG, site=SITE):
        self.store, self.log, self.site = store, log, site
        self.apps, self.aliases = load_routes(site)
        self.routes_at = time.time()
        self.lines = 0

    def parse(self, line):
        """One log line -> (app, minute, vid, country, ts) or None."""
        try:
            e = json.loads(line)
        except ValueError:
            return None
        req = e.get("request") or {}
        h = req.get("headers") or {}
        ua = _hdr(h, "User-Agent")
        ip = _hdr(h, "Cf-Connecting-Ip") or req.get("client_ip") or req.get("remote_ip", "")
        if not ua or BOT.search(ua):
            return None
        if not _hdr(h, "Cf-Connecting-Ip") and _internal(ip):
            return None  # health checks, screenshots, the box talking to itself
        app = app_of(req.get("uri", "/"), self.apps, self.aliases)
        if not app:
            return None
        ts = float(e.get("ts") or time.time())
        return app, int(ts // 60), self.store.vid(ip, ua), _hdr(h, "Cf-Ipcountry"), int(ts)

    def step(self):
        """Read whatever was appended since last time. Handles rotation."""
        if time.time() - self.routes_at > 300:
            self.apps, self.aliases = load_routes(self.site)
            self.routes_at = time.time()
        try:
            st = os.stat(self.log)
        except OSError:
            return 0
        inode, offset = self.store.cursor(self.log)
        if inode != st.st_ino or st.st_size < offset:
            offset = 0  # rotated or truncated: start the new file from the top
        if st.st_size == offset:
            return 0
        agg, vis = {}, {}
        with open(self.log, "rb") as f:
            f.seek(offset)
            chunk = f.read(32 * 1024 * 1024)
            end = chunk.rfind(b"\n") + 1
            for raw in chunk[:end].splitlines():
                p = self.parse(raw)
                if not p:
                    continue
                app, minute, vid, country, ts = p
                agg[(app, minute, vid)] = agg.get((app, minute, vid), 0) + 1
                v = vis.get(vid)
                vis[vid] = (vid, min(v[1], ts) if v else ts, max(v[2], ts) if v else ts,
                            country or (v[3] if v else ""))
        if agg:
            self.store.add([(*k, n) for k, n in agg.items()], list(vis.values()))
        self.store.set_cursor(self.log, st.st_ino, offset + end)
        self.lines += end and chunk[:end].count(b"\n")
        return len(agg)

    def run(self, every=2.0):
        n = 0
        while True:
            try:
                while self.step():
                    pass
                n += 1
                if n % 1800 == 0:
                    self.store.prune()
            except Exception as e:  # noqa: BLE001 — never let the tail die
                print("traffic collector:", e, flush=True)
            time.sleep(every)


# ── queries ─────────────────────────────────────────────────────────────

RANGES = {  # range -> (span seconds, bucket seconds)
    "1h": (3600, 60), "6h": (6 * 3600, 300), "24h": (86400, 900),
    "7d": (7 * 86400, 3600), "30d": (30 * 86400, 6 * 3600),
}


def live(store, window=LIVE_WINDOW, now=None):
    now = now or time.time()
    since = int((now - window) // 60)
    rows = store.q("SELECT app, COUNT(DISTINCT vid), SUM(hits) FROM seen WHERE minute >= ? "
                   "GROUP BY app ORDER BY 2 DESC, 3 DESC", (since,))
    total = store.q("SELECT COUNT(DISTINCT vid) FROM seen WHERE minute >= ?", (since,))[0][0]
    return {"window": window, "total": total, "at": int(now),
            "apps": [{"app": a, "users": u, "hits": h} for a, u, h in rows]}


def series(store, range="24h", top=8, apps=None, now=None):
    """Distinct users per bucket, per app, over the range. Apps ranked by users
    in the range; `apps` (comma list) pins which ones come back."""
    rng = range if range in RANGES else "24h"
    span, step = RANGES[rng]
    now = int(now or time.time())
    end = (now // step + 1) * step
    start = end - span
    smin, per = start // 60, step // 60
    ranked = store.q("SELECT app, COUNT(DISTINCT vid), SUM(hits) FROM seen WHERE minute >= ? "
                     "GROUP BY app ORDER BY 2 DESC, 3 DESC", (smin,))
    pick = [a for a in (apps.split(",") if isinstance(apps, str) else apps or []) if a]
    names = pick or [a for a, _, _ in ranked[:int(top)]]
    buckets = [start + i * step for i in builtins.range(span // step)]
    out = {a: [0] * len(buckets) for a in names}
    total = [0] * len(buckets)
    for app, b, users in store.q(
            "SELECT app, (minute - ?) / ? AS b, COUNT(DISTINCT vid) FROM seen "
            "WHERE minute >= ? GROUP BY app, b", (smin, per, smin)):
        if app in out and 0 <= b < len(buckets):
            out[app][b] = users
    for b, users in store.q(
            "SELECT (minute - ?) / ? AS b, COUNT(DISTINCT vid) FROM seen WHERE minute >= ? "
            "GROUP BY b", (smin, per, smin)):
        if 0 <= b < len(buckets):
            total[b] = users
    lv = {r["app"]: r["users"] for r in live(store, now=now)["apps"]}
    prev_smin = smin - (span // 60)
    prev_rows = store.q("SELECT app, COUNT(DISTINCT vid) FROM seen WHERE minute >= ? AND minute < ? GROUP BY app",
                        (prev_smin, smin))
    prev_users = {a: u for a, u in prev_rows}
    return {"range": rng, "step": step, "buckets": buckets, "total": total,
            "series": [{"app": a, "values": out[a]} for a in names],
            "table": [{"app": a, "users": u, "hits": h, "live": lv.get(a, 0), "prev": prev_users.get(a, 0)}
                      for a, u, h in ranked],
            "first": (store.q("SELECT MIN(minute) FROM seen")[0][0] or 0) * 60}


def app_series(store, range="24h", now=None):
    """Every app's per-bucket users at once — the sparklines in the table."""
    span, step = RANGES.get(range, RANGES["24h"])
    now = int(now or time.time())
    end = (now // step + 1) * step
    smin, per, n = (end - span) // 60, step // 60, span // step
    out = {}
    for app, b, users in store.q(
            "SELECT app, (minute - ?) / ? AS b, COUNT(DISTINCT vid) FROM seen "
            "WHERE minute >= ? GROUP BY app, b", (smin, per, smin)):
        if 0 <= b < n:
            out.setdefault(app, [0] * n)[b] = users
    return out


def countries(store, range="24h", now=None):
    """Distinct users by country over the range, top 25."""
    span, _ = RANGES.get(range, RANGES["24h"])
    now = int(now or time.time())
    smin = (now - span) // 60
    rows = store.q(
        "SELECT v.country, COUNT(DISTINCT s.vid) FROM seen s JOIN visitor v ON s.vid=v.vid "
        "WHERE s.minute >= ? AND v.country != '' GROUP BY v.country ORDER BY 2 DESC LIMIT 25",
        (smin,))
    return {"range": range, "countries": [{"code": c, "users": u} for c, u in rows]}


def heatmap(store, range="30d", now=None):
    """Distinct users by hour-of-day × day-of-week over the range."""
    span, _ = RANGES.get(range, RANGES["30d"])
    now = int(now or time.time())
    smin = (now - span) // 60
    rows = store.q(
        "SELECT CAST(strftime('%w', datetime(minute*60,'unixepoch')) AS INTEGER) AS dow, "
        "CAST(strftime('%H', datetime(minute*60,'unixepoch')) AS INTEGER) AS hr, "
        "COUNT(DISTINCT vid) FROM seen WHERE minute >= ? GROUP BY dow, hr",
        (smin,))
    grid = [[0] * 24 for _ in builtins.range(7)]
    for dow, hr, u in rows:
        grid[dow][hr] = u
    return {"range": range, "grid": grid,
            "days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]}
