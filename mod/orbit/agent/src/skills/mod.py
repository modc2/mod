"""
skills - what the agent can LEARN, as opposed to what it can call

A tool is a capability: bash runs a command, edit changes a file. A skill is a
method: a SKILL.md that says when to reach for those tools and how to use them
for one particular job. The agent already had a registry for the first thing.
This is the registry for the second, and it makes skills the fifth swappable
component of the box, beside the prompt, the model, the toolbox and memory.

The catalog itself lives in the `skills` module — the marketplace that scrapes
the open web for these documents and files them under ~/.mod/skills. This class
is the agent's view of it:

    skills = Skills()
    skills.ls()                     # what this host has learned
    skills.search("pdf forms")      # scan the web for more
    skills.install("gh:anthropics/skills:skills/pdf")
    skills.load(["pdf"])            # the documents, ready for a prompt

Three ways to reach the market, tried in order — in-process through the fleet,
over HTTP to a running server, then not at all. The last one is not an error:
an agent whose marketplace is down loses the ability to learn something new,
not the ability to answer, so every read degrades to an empty catalog with a
reason attached. That is the same rule the memory registry follows when the
module it was built against has moved.

A skill grants nothing. `tools:` in its front matter names tools the agent
already has, and loading one filters that list against the live registry — a
skill that asks for a tool this agent was not given simply does not get it.
"""
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

try:
    import mod as m
except ImportError:
    m = None

MODULE = os.environ.get("AGENT_SKILLS_MODULE", "skills")
URL = os.environ.get("AGENT_SKILLS_URL", "http://127.0.0.1:50860")
TIMEOUT = int(os.environ.get("AGENT_SKILLS_TIMEOUT", "20"))


class Skills:
    """The agent's view of the skill marketplace."""
    description = "Skill registry - the SKILL.md documents this agent can learn from"

    def __init__(self, module: str = None, url: str = None, timeout: int = TIMEOUT):
        self.module = module or MODULE
        self.url = (url or URL).rstrip("/")
        self.timeout = timeout
        self._mod = None
        self._why: Optional[str] = None

    # ── reaching the market ──────────────────────────────────────────

    def market(self):
        """The skills module, in this process, or None with a reason kept."""
        if self._mod is not None:
            return self._mod
        if m is None:
            self._why = "the mod framework is not importable here"
            return None
        try:
            self._mod = m.mod(self.module)()
            self._why = None
        except Exception as e:
            self._why = f"{self.module} module unavailable ({e})"
            self._mod = None
        return self._mod

    def _http(self, path: str, params: Dict = None, method: str = "GET",
              body: Dict = None) -> Any:
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "", [])})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _call(self, op: str, path: str, params: Dict = None,
              method: str = "GET", **kwargs) -> Any:
        """One operation, whichever way the market is reachable.

        In-process first: it is faster, it needs no server running, and it is
        the only way that works when the module is scaled to zero.
        """
        market = self.market()
        if market is not None:
            try:
                return getattr(market, op)(**kwargs)
            except AttributeError:
                self._why = f"{self.module} has no operation {op}"
            except Exception as e:
                self._why = f"{self.module}.{op} failed ({e})"
        try:
            return self._http(path, params, method,
                              kwargs if method != "GET" else None)
        except Exception as e:
            self._why = self._why or f"{self.url} unreachable ({e})"
            raise RuntimeError(self._why)

    def available(self) -> bool:
        try:
            self._call("health", "/health")
            return True
        except Exception:
            return False

    def why(self) -> Optional[str]:
        """Why the catalog is empty, when it is — for the console and /parts."""
        return self._why

    # ── reads (never raise: an empty catalog is a valid answer) ──────

    def items(self, q: str = "") -> List[Dict[str, Any]]:
        """Catalog cards — name, description, the tools each one expects."""
        try:
            got = self._call("installed", "/installed", {"q": q}, q=q)
            return got.get("skills", []) if isinstance(got, dict) else []
        except Exception:
            return []

    def ls(self) -> List[str]:
        return [s.get("name", "") for s in self.items() if s.get("name")]

    def exists(self, name: str) -> bool:
        return name in self.ls()

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """One skill with its markdown, or None."""
        try:
            doc = self._call("doc", "/doc", {"name": name}, name=name)
            return doc if isinstance(doc, dict) and doc.get("markdown") else None
        except Exception:
            return None

    def load(self, names: List[str] = None, q: str = "") -> List[Dict[str, Any]]:
        """Several skills with bodies — what goes in front of the model."""
        try:
            got = self._call("load", "/load",
                             {"names": ",".join(names or []), "q": q},
                             names=names, q=q)
            return got.get("skills", []) if isinstance(got, dict) else []
        except Exception:
            return []

    # ── writes (these DO raise: a failed install must be visible) ────

    def search(self, q: str = "", sources: List[str] = None, limit: int = 30,
               fresh: bool = False) -> Dict[str, Any]:
        """Scan the web for skills — the marketplace's own search."""
        return self._call("search", "/search",
                          {"q": q, "sources": ",".join(sources or []),
                           "limit": limit, "fresh": "1" if fresh else ""},
                          q=q, sources=sources, limit=limit, fresh=fresh)

    def install(self, id: str, name: str = None, all: bool = False,
                who: str = "") -> Dict[str, Any]:
        """Install a skill into this host's catalog. Writes markdown, runs nothing."""
        return self._call("install", "/install", method="POST",
                          id=id, name=name, all=all, who=who)

    def write(self, name: str, body: str, description: str = "",
              tools: List[str] = None, tags: List[str] = None,
              who: str = "") -> Dict[str, Any]:
        """Author a skill from the console rather than finding one."""
        return self._call("write", "/write", method="POST",
                          name=name, body=body, description=description,
                          tools=tools, tags=tags, who=who)

    def remove(self, name: str, who: str = "") -> Dict[str, Any]:
        return self._call("remove", f"/installed/{urllib.parse.quote(name)}",
                          method="DELETE", name=name)

    # ── the card the console and /parts read ────────────────────────

    def status(self) -> Dict[str, Any]:
        items = self.items()
        return {
            "module": self.module,
            "installed": len(items),
            "skills": [{"name": s.get("name"), "description": s.get("description", ""),
                        "tools": s.get("tools") or [], "tags": s.get("tags") or [],
                        "source": s.get("source")} for s in items],
            "available": bool(items) or self.available(),
            **({"why": self._why} if self._why else {}),
        }

    def forward(self, name: str = None, **kwargs) -> Any:
        """
        forward()            -> the catalog
        forward('pdf')       -> one skill, with its document
        forward(action=…)    -> any operation by name
        """
        action = kwargs.pop("action", None)
        if action:
            fn = getattr(self, action, None)
            if not callable(fn) or action.startswith("_"):
                raise KeyError(f"no such operation: {action}")
            return fn(**kwargs)
        if name:
            return self.get(name)
        return self.status()

    def test(self) -> Dict[str, Any]:
        """Works with the market up or down — the second case is the point."""
        assert isinstance(self.ls(), list)
        assert isinstance(self.items(), list)
        dead = Skills(module="no-such-module", url="http://127.0.0.1:1")
        assert dead.ls() == [] and dead.get("anything") is None
        assert dead.load(["x"]) == []
        assert dead.why(), "a dead market must say why it is empty"
        assert dead.status()["installed"] == 0
        try:
            dead.install("gh:x/y")
            assert False, "a failed install must raise, not return empty"
        except RuntimeError:
            pass
        return {"passed": True, "installed": len(self.ls()),
                "market": self.module, "why": self.why()}
