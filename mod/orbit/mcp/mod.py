"""mcp — MCP Hub client.

Thin Python face over the Rust API (mcp-rs, :50360). The hub aggregates every
MCP server it knows — auto-discovered fleet mods plus user-registered remotes —
and re-exposes the union at POST /mcp as one MCP server (tools named
server__tool). These fns mirror the REST surface for mod-protocol callers.

Write calls (add_server / remove_server / toggle_server) send Bearer
~/.mod/mcp/server.secret automatically when that file exists.
"""

import json
import os
import urllib.parse
import urllib.request

API = os.environ.get("MCP_API_URL", "http://localhost:50360")
HUB_DIR = os.environ.get("MCP_HUB_DIR", os.path.expanduser("~/.mod/mcp"))


def _secret():
    try:
        with open(os.path.join(HUB_DIR, "server.secret")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _q(s):
    return urllib.parse.quote(str(s), safe="")


def _req(path, body=None, method=None, auth=False):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if auth:
        s = _secret()
        if s:
            req.add_header("Authorization", "Bearer " + s)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def info():
    """Hub identity, endpoint map and server count (the null call)."""
    return _req("/")


def servers():
    """Every aggregated server with its live probe."""
    return _req("/servers")["servers"]


def server(id):
    """One server row (registry + probe)."""
    return _req(f"/servers/{id}")


def add_server(url, id=None, name=None, headers=None, note=None, force=False):
    """Probe-then-register a remote MCP server on the hub."""
    body = {"url": url, "force": force}
    if id:
        body["id"] = id
    if name:
        body["name"] = name
    if headers:
        body["headers"] = headers
    if note:
        body["note"] = note
    return _req("/servers", body, auth=True)


def remove_server(id):
    """Unregister a user server / disable a fleet server."""
    return _req(f"/servers/{id}", method="DELETE", auth=True)


def toggle_server(id, enabled=True):
    """Include or exclude a server from aggregation."""
    return _req(f"/servers/{id}/toggle", {"enabled": bool(enabled)}, auth=True)


def probe(url, headers=None):
    """Ad-hoc MCP handshake against any URL; returns serverInfo + tools."""
    return _req("/probe", {"url": url, "headers": headers or {}})


def tools(server=None):
    """The aggregated, namespaced tool list (optionally one server's)."""
    q = f"?server={server}" if server else ""
    return _req("/tools" + q)


def call(tool, args=None, server=None):
    """Call any aggregated tool: call('chutes__chat', {...}) or call('chat', server='chutes')."""
    body = {"tool": tool, "args": args or {}}
    if server:
        body["server"] = server
    return _req("/call", body)


def client_config(client="json"):
    """Paste-ready MCP client config pointing at the hub."""
    return _req(f"/client_config?client={client}")


def stats():
    """Servers, up/down, aggregated tool total, by_source."""
    return _req("/stats")


def refresh(id=None, wake=True):
    """Re-probe one server, or re-scan the fleet and re-probe everything.

    A single-server re-probe wakes the mod through the activator when its own
    port is refusing — pass wake=False to leave a sleeping mod asleep.
    """
    if id:
        return _req(f"/servers/{id}/refresh?wake={'1' if wake else '0'}", {})
    return _req("/refresh", {})


def discover():
    """Live sweep: knock on every fleet port and adopt whatever speaks MCP.

    The companion to config-declared discovery — it finds mods serving /mcp
    that never said so in their config.json.
    """
    return _req("/discover", {})


def search(q, count=8, provider=None):
    """Search the web. No API key needed; a configured one is preferred."""
    path = f"/search?q={_q(q)}&count={int(count)}"
    if provider:
        path += f"&provider={_q(provider)}"
    return _req(path)


def fetch(url, max_chars=8000):
    """Read one public URL as text (HTML stripped)."""
    return _req(f"/fetch?url={_q(url)}&max_chars={int(max_chars)}")


def catalog(q="", registry="all", limit=20):
    """Search the public MCP directories for servers to connect."""
    return _req(f"/catalog?q={_q(q)}&registry={_q(registry)}&limit={int(limit)}")


def intake(text):
    """Parse a URL, CID, client config, `claude mcp add` line or QR payload
    into candidate servers. Nothing is registered."""
    return _req("/intake", {"text": text})


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
