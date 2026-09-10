"""mcpscan — the internet-wide MCP index.

Thin Python face over the Rust API (scan-rs, :50700). The module crawls every
public MCP directory, probes every endpoint it finds on a permanent loop, and
hunts for endpoints nobody published. These fns mirror the REST surface for
mod-protocol callers.

    m mcpscan/search q="github issues" status=live
    m mcpscan/call server=... tool=... args='{...}'

Crawl and hunt send Bearer ~/.mod/mcpscan/server.secret automatically when that
file exists; everything else is open.
"""

import json
import os
import urllib.parse
import urllib.request

API = os.environ.get("MCPSCAN_API_URL", "http://localhost:50700")
STATE_DIR = os.environ.get("MCPSCAN_DIR", os.path.expanduser("~/.mod/mcpscan"))


def _secret():
    try:
        with open(os.path.join(STATE_DIR, "server.secret")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _req(path, body=None, method=None, auth=False, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if auth:
        s = _secret()
        if s:
            req.add_header("Authorization", "Bearer " + s)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _qs(**kw):
    q = {k: v for k, v in kw.items() if v not in (None, "", False)}
    return ("?" + urllib.parse.urlencode(q)) if q else ""


def info():
    """Index identity, endpoint map and how many servers are indexed."""
    return _req("/")


def stats():
    """Index size, live/auth/down split, per-directory counts, scraper telemetry."""
    return _req("/stats")


def search(q="", status=None, source=None, sort=None, limit=25, offset=0, tools=False):
    """Search every indexed server by name, description or tool name."""
    return _req("/catalog" + _qs(q=q, status=status, source=source, sort=sort,
                                 limit=limit, offset=offset, tools="1" if tools else None))


def server(id):
    """One indexed server: endpoint, directories, packages, last probe, tools."""
    return _req("/catalog/" + urllib.parse.quote(id))


def sources():
    """Per-directory crawl reports — rows found, new, duration, or the key still needed."""
    return _req("/sources")


def recent(limit=25):
    """The scraper's live feed: servers whose status just changed."""
    return _req("/recent" + _qs(limit=limit))["events"]


def crawl(source=None, wait=False):
    """Re-read every public directory now (or just one)."""
    body = {"wait": bool(wait)}
    if source:
        body["source"] = source
    return _req("/crawl", body, auth=True, timeout=900 if wait else 120)


def hunt(budget=12):
    """Knock on /mcp, /sse … at domains of servers that never published an endpoint."""
    return _req("/hunt", {"budget": int(budget)}, auth=True, timeout=300)


def probe(url, headers=None):
    """Handshake with any MCP endpoint, index the result, return its tools."""
    return _req("/probe", {"url": url, "headers": headers or {}})


def reprobe(id):
    """Re-probe one indexed server now."""
    return _req("/catalog/" + urllib.parse.quote(id) + "/probe", {})


def call(server, tool, args=None, headers=None):
    """Call a tool on any indexed server — nothing has to be registered first."""
    return _req("/call", {"server": server, "tool": tool,
                          "args": args or {}, "headers": headers or {}}, timeout=180)


def export(status="live", format="json", limit=5000):
    """The index as data, or a paste-ready mcpServers config of every live server."""
    return _req("/export" + _qs(status=status, format=format, limit=limit))


def client_config(client="json"):
    """Paste-ready MCP client config pointing at this index."""
    return _req("/client_config" + _qs(client=client))


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
