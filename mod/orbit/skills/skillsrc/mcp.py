"""mcp — the same market, as a Model Context Protocol server.

Ten tools, each one a method on Market. An MCP client and a curl get the same
answers because there is one implementation behind both; this file is the
tool schema and the dispatch, and nothing else.

Streamable HTTP lives in api.py (POST /mcp); this module is transport-free so
a stdio client can import it directly.
"""
import json
from typing import Any, Dict, List

from .market import Market
from .sources import SOURCE_IDS

PROTOCOL = "2025-06-18"
SERVER = {"name": "skills", "version": "0.1.0",
          "title": "Skills — the agent skill marketplace"}

_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_LIST = {"type": "array", "items": {"type": "string"}}

TOOLS: List[Dict[str, Any]] = [
    {"name": "skills_search",
     "description": ("Scan the open web for agent skills. One query fans out to the "
                     "official anthropics/skills catalog, GitHub repo search, skill "
                     "topics, GitHub code search for SKILL.md files, curated "
                     "awesome-lists and the modules on this host; duplicates merge "
                     "and results are ranked. Returns cards, not documents — open one "
                     "with skills_get."),
     "inputSchema": {"type": "object", "properties": {
         "q": dict(_STR, description="what the skill should do, e.g. 'pdf forms'"),
         "sources": dict(_LIST, description=f"limit to some of: {', '.join(SOURCE_IDS)}"),
         "limit": dict(_INT, description="max results (default 30)"),
         "fresh": dict(_BOOL, description="bypass the cache")}}},
    {"name": "skills_sources",
     "description": "The sources a scan reaches, and which are ready (code search needs a token).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "skills_get",
     "description": ("Open one search result and read the actual SKILL.md behind it. "
                     "Takes a result id (gh:owner/repo[:path], mod:orbit/name, or a URL) "
                     "or the name of an installed skill."),
     "inputSchema": {"type": "object", "properties": {
         "id": dict(_STR, description="result id, URL, or installed skill name"),
         "path": dict(_STR, description="path inside the repo, if the id is a bare repo")},
         "required": ["id"]}},
    {"name": "skills_install",
     "description": ("Install a skill into this host's catalog as a SKILL.md document. "
                     "Nothing is executed — a skill is instructions, never code. Use "
                     "all=true to take every skill in a repo (most packs hold several)."),
     "inputSchema": {"type": "object", "properties": {
         "id": dict(_STR, description="result id from skills_search"),
         "path": dict(_STR, description="path inside the repo"),
         "name": dict(_STR, description="rename it in the catalog"),
         "all": dict(_BOOL, description="install every skill in the repo")},
         "required": ["id"]}},
    {"name": "skills_installed",
     "description": "The installed catalog — every skill on this host, as cards.",
     "inputSchema": {"type": "object", "properties": {
         "q": dict(_STR, description="filter by text"),
         "tag": dict(_STR, description="filter by tag")}}},
    {"name": "skills_doc",
     "description": ("The markdown of one skill — what you actually put in front of a "
                     "model. Works on an installed name or on any result id, in which "
                     "case it is fetched live and not installed."),
     "inputSchema": {"type": "object", "properties": {
         "name": dict(_STR, description="installed skill name, or a result id")},
         "required": ["name"]}},
    {"name": "skills_load",
     "description": ("Several skills at once, bodies included — the call an agent makes "
                     "at the start of a run to learn what it is allowed to do. Name them, "
                     "or filter with q; unfiltered it returns the whole catalog."),
     "inputSchema": {"type": "object", "properties": {
         "names": dict(_LIST, description="skill names to load"),
         "q": dict(_STR, description="load everything matching this instead")}}},
    {"name": "skills_write",
     "description": ("Author a skill here instead of finding one: a name, a description "
                     "and the markdown instructions. Saved in the same catalog and in the "
                     "same format as a scraped one."),
     "inputSchema": {"type": "object", "properties": {
         "name": _STR, "body": dict(_STR, description="the instructions, as markdown"),
         "description": dict(_STR, description="one line — when should a model reach for this"),
         "tools": dict(_LIST, description="tools this skill expects the agent to have"),
         "tags": _LIST},
         "required": ["name", "body"]}},
    {"name": "skills_remove",
     "description": "Remove an installed skill from the catalog.",
     "inputSchema": {"type": "object", "properties": {"name": _STR},
                     "required": ["name"]}},
    {"name": "skills_token",
     "description": ("Set (or clear) the GitHub token that unlocks code search and lifts "
                     "the anonymous rate limit. Stored 0600 off-tree, never in the module."),
     "inputSchema": {"type": "object", "properties": {
         "token": dict(_STR, description="a GitHub PAT; empty string clears it")}}},
]

WRITE_TOOLS = {"skills_install", "skills_write", "skills_remove", "skills_token"}

_market: Market = None


def market() -> Market:
    global _market
    if _market is None:
        _market = Market()
    return _market


def call_tool(name: str, args: Dict[str, Any]) -> Any:
    m = market()
    a = args or {}
    if name == "skills_search":
        return m.search(a.get("q", ""), a.get("sources"), int(a.get("limit") or 30),
                        bool(a.get("fresh")))
    if name == "skills_sources":
        return m.sources()
    if name == "skills_get":
        return m.get(a.get("id"), a.get("path"))
    if name == "skills_install":
        return m.install(a.get("id"), a.get("path"), a.get("name"),
                         all=bool(a.get("all")), who=a.get("who", ""))
    if name == "skills_installed":
        return m.installed(a.get("q", ""), a.get("tag"))
    if name == "skills_doc":
        return m.doc(a.get("name"))
    if name == "skills_load":
        return m.publish(a.get("names"), a.get("q", ""))
    if name == "skills_write":
        return m.write(a.get("name"), a.get("body"), a.get("description", ""),
                       a.get("tools"), a.get("tags"), who=a.get("who", ""))
    if name == "skills_remove":
        return m.remove(a.get("name"))
    if name == "skills_token":
        return m.token(a.get("token"))
    raise KeyError(f"unknown tool: {name}")


def info() -> Dict[str, Any]:
    return {"module": "skills", "protocol": PROTOCOL, "server": SERVER,
            "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
            "sources": SOURCE_IDS}


def rpc(req: Dict[str, Any], authorized: bool = True) -> Dict[str, Any]:
    """One JSON-RPC message in, one out. `None` for a notification."""
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "serverInfo": SERVER,
                   "capabilities": {"tools": {"listChanged": False}}})
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name in WRITE_TOOLS and not authorized:
            return err(-32001, "installing, writing or removing a skill is gated — "
                               "call from this box or send the bearer token")
        try:
            result = call_tool(name, args)
        except KeyError as e:
            return err(-32601, str(e))
        except Exception as e:
            return ok({"content": [{"type": "text", "text": f"error: {e}"}],
                       "isError": True})
        text = json.dumps(result, indent=2, default=str)
        if len(text) > 200_000:
            text = text[:200_000] + "\n… truncated"
        return ok({"content": [{"type": "text", "text": text}]})
    return err(-32601, f"unknown method: {method}")
