"""
agenthub - every agent we can reach, searched by meaning, each one a module.

Three kinds of agent show up in one list:

    persona   an agent in this module's registry (src/agents/) — a prompt,
              a model and a toolbox over our own loop
    github    an open-source agent on GitHub: the curated catalog (agents
              whose headless command we know, catalog.py) plus whatever
              GitHub's agent topics turn up, starred and cached
    mod       a GitHub agent already installed here as its own module

search() hands that list to orbit/modsearch (LFM2.5 embeddings + BM25, local)
and falls back to a word match when modsearch is down, so search never fails —
it says which mode answered.

install() turns a GitHub agent into a module of its own under orbit/<name>:
the template/ runtime (mod.py, api.py, auth.py, index.html — identical in every
generated module) plus a config.json carrying the agent's recipe. That module
then stands alone under the mod protocol:

    m <name>                      its card;  m <name> 'task'  runs it
    {host}/<name>                 its own interface (install, keys, run)
    {host}/<name>/api/agents      the probe orbit/build mounts agents by
    POST .../run/stream           the run, as the events build renders

and it is registered here as a harness with a persona in front of it, so the
agent console can hand a run to it like it hands one to Claude Code.

Nothing is installed system-wide: the agent's own packages go in
~/.mod/<name>/ when its owner presses Install. Scaffolding writes the module
directory only.

State: ~/.mod/agent/agenthub/{github.json, installed.json}
"""
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .catalog import CATALOG, NATIVE

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template"
ORBIT = HERE.parents[2]                     # .../orbit
CORE = ORBIT.parent / "core"
STATE = Path(os.path.expanduser("~/.mod/agent/agenthub"))

PORTS = range(51200, 51400)                 # generated modules live here
GITHUB_TTL = 6 * 3600
MODSEARCH = os.environ.get("MODSEARCH_URL", "http://127.0.0.1:51090")
UA = "mod-agent-agenthub/1.0"
TEMPLATE_FILES = ("mod.py", "api.py", "auth.py", "index.html", "_serve.sh")

# what GitHub discovery asks. Keyless search is 10 calls/min, so few and broad.
TOPIC_QUERIES = [
    "topic:coding-agent stars:>300",
    "topic:ai-agent stars:>3000",
    "topic:autonomous-agents stars:>1000",
    "topic:ai-agents stars:>3000",
    "topic:agentic-ai stars:>1000",
]
_WORD = re.compile(r"[a-z0-9]+")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "agent"


def doc_text(item: Dict[str, Any]) -> str:
    """What search embeds for one agent. Stable, so modsearch's vector cache
    keeps hitting between calls."""
    return ". ".join(x for x in [
        item.get("label") or item.get("id"), item.get("description") or "",
        " ".join(item.get("tags") or []), item.get("kind") or "",
        item.get("repo") or ""] if x)


class AgentHub:
    description = "Search every agent by meaning; install GitHub agents as their own modules"

    def __init__(self, owner: str = None, is_owner: Callable[[Any], bool] = None,
                 addr: Callable[[Any], Optional[str]] = None, agents=None,
                 harness=None, gh_token: Callable[[], Optional[str]] = None):
        self.owner = (owner or "").lower() or None
        self._is_owner = is_owner or (lambda key: False)
        self._addr = addr or (lambda key: None)
        self.agents = agents
        self.harness = harness
        self._gh_token = gh_token or (lambda: os.environ.get("GITHUB_TOKEN"))
        self._lock = threading.Lock()
        self._refreshing = False

    # ── state ────────────────────────────────────────────────────────

    @staticmethod
    def _read(name: str, default):
        try:
            return json.loads((STATE / name).read_text())
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write(name: str, data):
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = STATE / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(STATE / name)

    def installed(self) -> Dict[str, Dict[str, Any]]:
        """id -> {mod, harness, port, path, persona, ...}, pruned of modules
        whose directory has since been deleted by hand."""
        got = self._read("installed.json", {})
        return {k: v for k, v in got.items() if Path(v.get("path", "")).is_dir()}

    def runners(self) -> Dict[str, str]:
        """harness name -> module, for Harness to merge into its RUNNERS."""
        return {v["harness"]: v["mod"] for v in self.installed().values() if v.get("harness")}

    # ── github ───────────────────────────────────────────────────────

    def _gh(self, query: str, per_page: int = 50) -> List[Dict[str, Any]]:
        headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
        tok = self._gh_token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        r = requests.get("https://api.github.com/search/repositories",
                         params={"q": query, "sort": "stars", "order": "desc",
                                 "per_page": per_page}, headers=headers, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"GitHub {r.status_code}: {r.text[:160]}")
        return r.json().get("items") or []

    def discover(self, fresh: bool = False) -> Dict[str, Any]:
        """Agents on GitHub, by topic, plus live stars for the catalog.
        Cached GITHUB_TTL; a failed query keeps what the others returned."""
        cached = self._read("github.json", {})
        if cached and not fresh and time.time() - cached.get("at", 0) < GITHUB_TTL:
            return cached
        repos: Dict[str, Dict[str, Any]] = {}
        errors = []
        known = " ".join(f"repo:{c['repo']}" for c in CATALOG + NATIVE)
        for q in [known] + TOPIC_QUERIES:
            try:
                for r in self._gh(q, per_page=50 if q != known else 100):
                    repos.setdefault(r["full_name"].lower(), r)
            except Exception as e:
                errors.append(f"{q[:40]}: {e}")
        items = []
        for full, r in repos.items():
            items.append({
                "repo": r["full_name"], "stars": r.get("stargazers_count", 0),
                "label": r.get("name"), "description": (r.get("description") or "")[:400],
                "tags": (r.get("topics") or [])[:12], "language": r.get("language"),
                "pushed_at": r.get("pushed_at"), "url": r.get("html_url"),
                "license": (r.get("license") or {}).get("spdx_id"),
                "archived": r.get("archived", False)})
        out = {"at": time.time(), "repos": items, "errors": errors}
        if items or not cached:
            self._write("github.json", out)
            return out
        return {**cached, "errors": errors}

    def _refresh_bg(self):
        """Refresh discovery in the background when the cache is stale; the
        caller is never held for GitHub."""
        cached = self._read("github.json", {})
        if time.time() - cached.get("at", 0) < GITHUB_TTL or self._refreshing:
            return
        self._refreshing = True

        def work():
            try:
                self.discover(fresh=True)
            except Exception:
                pass
            finally:
                self._refreshing = False
        threading.Thread(target=work, daemon=True).start()

    # ── the one list ─────────────────────────────────────────────────

    def catalog(self, refresh: bool = True) -> List[Dict[str, Any]]:
        """Every GitHub agent: curated first, then discovered, each marked
        with what is installed here."""
        if refresh:
            self._refresh_bg()
        gh = {r["repo"].lower(): r for r in self._read("github.json", {}).get("repos", [])}
        inst = self.installed()
        items, seen = [], set()
        for c in CATALOG + NATIVE:
            live = gh.get(c["repo"].lower(), {})
            items.append(self._card({**c, "stars": live.get("stars"),
                                     "language": live.get("language"),
                                     "pushed_at": live.get("pushed_at")}, inst))
            seen.add(c["repo"].lower())
        taken = {i["id"] for i in items}
        for full, r in sorted(gh.items(), key=lambda kv: -(kv[1].get("stars") or 0)):
            if full in seen or r.get("archived"):
                continue
            sid = slug(r["label"])
            if sid in taken:
                sid = slug(r["repo"].replace("/", "-"))
            taken.add(sid)
            items.append(self._card({"id": sid, "kind": "github", **r}, inst))
        return items

    @staticmethod
    def _card(c: Dict[str, Any], inst: Dict[str, Dict]) -> Dict[str, Any]:
        rec = inst.get(c["id"])
        return {**c, "source": "github", "url": c.get("url") or f"https://github.com/{c['repo']}",
                "runnable": bool(c.get("run") or c.get("native")),
                "installed": bool(rec or c.get("native")),
                "mod": (rec or {}).get("mod") or c.get("native"),
                "harness": (rec or {}).get("harness") or c.get("harness"),
                "interface": (f"http://127.0.0.1:{rec['port']}/" if rec else None)}

    def item(self, id: str) -> Dict[str, Any]:
        for c in self.catalog(refresh=False):
            if c["id"] == id or c["repo"].lower() == (id or "").lower():
                return c
        raise KeyError(f"no agent {id!r} in the hub — search first, or install by repo=owner/name")

    def _personas(self) -> List[Dict[str, Any]]:
        if not self.agents:
            return []
        out = []
        for name in self.agents.ls():
            try:
                a = self.agents.get(name)
            except Exception:
                continue
            out.append({"id": name, "source": "persona", "kind": "persona",
                        "label": a.get("name") or name, "description": a.get("description", ""),
                        "icon": a.get("icon"), "harness": a.get("harness"),
                        "tags": [t for t in [a.get("harness"), a.get("provider")] if t]})
        return out

    def everything(self, sources: List[str] = None) -> List[Dict[str, Any]]:
        sources = sources or ["persona", "github"]
        rows = []
        if "github" in sources:
            rows += self.catalog()
        if "persona" in sources:
            rows += self._personas()
        return rows

    # ── semantic search ──────────────────────────────────────────────

    def search(self, q: str = "", k: int = 20, sources: List[str] = None,
               installed: bool = None) -> Dict[str, Any]:
        """Agents ranked by what the query means, not the words in it.

        modsearch embeds the docs we hand it (LFM2.5 retrieval encoder, local)
        and fuses cosine with BM25; vectors are cached by text there, so only
        new or changed agents cost an embedding. When it is unreachable the
        word match answers, and `mode` says so.
        """
        rows = self.everything(sources)
        if installed is not None:
            rows = [r for r in rows if bool(r.get("installed")) == installed]
        by_key = {f"{r['source']}:{r['id']}": r for r in rows}
        q = (q or "").strip()
        if not q:
            return {"mode": "list", "query": q, "total": len(rows),
                    "results": rows[:k] if k else rows}
        docs = [{"id": key, "name": r.get("label") or r["id"], "text": doc_text(r)}
                for key, r in by_key.items()]
        try:
            res = requests.post(f"{MODSEARCH}/search", json={"query": q, "docs": docs, "k": k},
                                timeout=60).json()
            if res.get("mode") == "semantic" and res.get("results") is not None:
                hits = [{**by_key[h["id"]], "score": h.get("score"), "why": h.get("why", [])}
                        for h in res["results"] if h["id"] in by_key]
                return {"mode": "semantic", "model": res.get("model"), "query": q,
                        "total": len(rows), "results": hits}
            err = res.get("error") or res.get("mode")
        except Exception as e:
            err = str(e)
        return {**self._lexical(q, by_key, k), "fallback": err}

    @staticmethod
    def _lexical(q: str, by_key: Dict[str, Dict], k: int) -> Dict[str, Any]:
        words = set(_WORD.findall(q.lower()))
        scored = []
        for key, r in by_key.items():
            text = doc_text(r).lower()
            toks = _WORD.findall(text)
            hit = [w for w in words if w in toks]
            if not hit:
                continue
            name = (r.get("label") or r["id"]).lower()
            s = len(hit) / len(words) + (0.5 if any(w in name for w in words) else 0)
            s += 0.05 * math.log1p(r.get("stars") or 0)
            scored.append({**r, "score": round(s, 4), "why": hit})
        scored.sort(key=lambda r: -r["score"])
        return {"mode": "lexical", "query": q, "total": len(by_key), "results": scored[:k]}

    def warm(self):
        """Embed the list once in the background so the first real search is fast."""
        threading.Thread(target=lambda: self.search("coding agent", k=1), daemon=True).start()

    # ── install: a GitHub agent becomes a module ─────────────────────

    def _require_owner(self, key, op: str):
        if not self._is_owner(key):
            raise PermissionError(
                f"{op}: a generated agent module runs a third-party CLI on this "
                f"host's shell, so installing one is the host owner's call")

    @staticmethod
    def _config(path: Path) -> Dict[str, Any]:
        try:
            return json.loads((path / "config.json").read_text())
        except (OSError, ValueError):
            return {}

    def _ours(self, path: Path) -> bool:
        return self._config(path).get("generated_by") == "orbit/agent agenthub"

    def _mod_name(self, id: str) -> str:
        """The module name for an agent: its id, unless a module that isn't
        ours already holds it (core wins name collisions over orbit)."""
        for name in (id, f"{id}-agent", f"{id}-gh"):
            o, c = ORBIT / name, CORE / name
            if c.exists():
                continue
            if not o.exists() or self._ours(o):
                return name
        raise FileExistsError(f"no free module name for {id}")

    def _used_ports(self) -> set:
        used = set()
        for root in (ORBIT, CORE):
            for cfg in root.glob("*/config.json"):
                try:
                    c = json.loads(cfg.read_text())
                except (OSError, ValueError):
                    continue
                for k in ("port", "app_port", "api_port"):
                    if isinstance(c.get(k), int):
                        used.add(c[k])
        try:
            out = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True, timeout=5).stdout
            used |= {int(m) for m in re.findall(r":(\d+)\s", out)}
        except Exception:
            pass
        return used

    def _free_port(self) -> int:
        used = self._used_ports() | {v.get("port") for v in self.installed().values()}
        for p in PORTS:
            if p not in used:
                return p
        raise RuntimeError(f"no free port in {PORTS.start}-{PORTS.stop - 1}")

    def install(self, id: str = None, repo: str = None, key=None, start: bool = True,
                setup: bool = False, persona: bool = True) -> Dict[str, Any]:
        """Scaffold orbit/<name> for a GitHub agent and wire it in.

        id     a hub id (search result), or
        repo   any owner/name on GitHub — becomes a repo-only module whose
               owner sets its run command from its page
        start  serve its interface under pm2 now
        setup  also install the agent's own packages into ~/.mod/<name>/
               (minutes, and network) — otherwise its page has the button
        """
        self._require_owner(key, "install agent")
        if repo and not id:
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
                raise ValueError("repo must look like owner/name")
            try:
                item = self.item(repo)
            except KeyError:
                item = {"id": slug(repo.split("/")[1]), "repo": repo, "label": repo.split("/")[1],
                        "kind": "github", "description": f"{repo} from GitHub", "tags": [],
                        "install": {"kind": "git"}}
        else:
            item = self.item(id)
        if item.get("native"):
            return {"id": item["id"], "mod": item["native"], "harness": item.get("harness"),
                    "already": True, "note": f"{item['label']} is already a harness here via orbit/{item['native']}"}

        with self._lock:
            inst = self._read("installed.json", {})
            rec = inst.get(item["id"])
            name = rec["mod"] if rec and Path(rec.get("path", "")).is_dir() else self._mod_name(item["id"])
            path = ORBIT / name
            port = (rec or {}).get("port") or self._config(path).get("port") or self._free_port()
            path.mkdir(parents=True, exist_ok=True)
            for f in TEMPLATE_FILES:
                shutil.copy2(TEMPLATE / f, path / f)
            (path / "_serve.sh").chmod(0o755)
            spec = {k: item[k] for k in ("repo", "label", "install", "bin", "run", "model",
                                          "goal", "env", "kind") if item.get(k) is not None}
            prev = self._config(path)
            if prev.get("agent", {}).get("run") and not spec.get("run"):
                spec["run"] = prev["agent"]["run"]          # keep what the owner taught it
            installer = (self._addr(key) or "").lower() or None
            owner = self.owner or installer
            cfg = {
                "name": name, "version": prev.get("version", "0.1.0"),
                "description": item.get("description") or f"{item['label']} from GitHub",
                "icon": item.get("icon") or item["label"][:2],
                "color": item.get("color") or "#7aa2f7",
                "owner": owner,
                "owners": sorted({a for a in [installer] + list(prev.get("owners") or [])
                                  if a and a != owner}),
                "port": port, "route": prev.get("route", False), "base_path": f"/{name}",
                "urls": {"api": f"http://127.0.0.1:{port}", "app": f"http://127.0.0.1:{port}/"},
                "harness": {"name": item["id"], "label": item["label"], "bin": item.get("bin", "")},
                "agent": spec,
                "agent_contract": {"roster": "GET /agents", "run": "POST /run/stream (SSE)",
                                   "harness": "harness() + run(query, path=, goal=, model=, on_step=)"},
                "fns": ["forward", "run", "run_stream", "harness", "agents", "setup",
                        "setup_status", "env_keys", "set_env", "set_run", "readme", "info",
                        "health", "serve", "kill", "test"],
                "generated_by": "orbit/agent agenthub",
                "source": f"https://github.com/{item['repo']}",
            }
            (path / "config.json").write_text(json.dumps(cfg, indent=4) + "\n")
            (path / "README.md").write_text(self._readme(cfg, item))
            rec = {"id": item["id"], "mod": name, "harness": item["id"], "port": port,
                   "path": str(path), "repo": item["repo"], "by": installer,
                   "installed_at": (rec or {}).get("installed_at") or time.time()}
            inst[item["id"]] = rec
            self._write("installed.json", inst)

        if self.harness is not None:
            self.harness.RUNNERS[item["id"]] = name
            self.harness._loaded.pop(item["id"], None)
        out = {**rec, "interface": f"http://127.0.0.1:{port}/", "config": cfg}
        if persona and self.agents is not None:
            out["persona"] = self._persona(item, key)
        if start:
            out["serve"] = self._serve(path, port)
        if setup:
            out["setup"] = self._module(path).setup(wait=False)
        return out

    def _persona(self, item: Dict[str, Any], key) -> Dict[str, Any]:
        """A persona with harness=<id>, so the console can pick the agent."""
        pid = item["id"]
        try:
            if pid in self.agents.ls():
                return {"name": pid, "existing": True}
            a = self.agents.create(pid, description=(item.get("description") or "")[:200],
                                   goal="", icon=(item.get("icon") or ">_")[:2],
                                   harness=pid, key=key)
            return {"name": pid, "created": True, "cid": a.get("cid")}
        except Exception as e:
            return {"name": pid, "error": str(e)}

    @staticmethod
    def _serve(path: Path, port: int) -> Dict[str, Any]:
        proc = f"{path.name}-api"
        subprocess.run(["pm2", "delete", proc], capture_output=True)
        r = subprocess.run(["pm2", "start", str(path / "_serve.sh"), "--name", proc,
                            "--cwd", str(path)], capture_output=True, text=True,
                           env={**os.environ, "PORT": str(port)})
        return {"pm2": proc, "ok": r.returncode == 0, **({"error": r.stderr[-400:]} if r.returncode else {})}

    @staticmethod
    def _module(path: Path):
        """Load a generated module by file path — never by `import mod`, which
        from in here would find the agent's own mod.py."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"{path.name}_agentmod", path / "mod.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Mod()

    @staticmethod
    def _readme(cfg: Dict[str, Any], item: Dict[str, Any]) -> str:
        a = cfg["agent"]
        run = " ".join(a.get("run") or []) or "(none yet - set it from the module page)"
        return f"""# {cfg['name']}

{item['label']} ({cfg['source']}) as a mod-protocol module, generated by orbit/agent's agent hub.

{cfg['description']}

- interface: `{cfg['urls']['app']}` (and `{{host}}/{cfg['name']}` when routed)
- API: `GET /agents`, `POST /run/stream`, `GET /info`, `POST /setup`, `POST /env`
- run command: `{run}`
- install: into `~/.mod/{cfg['name']}/` only, from the interface or `m {cfg['name']}/setup`
- keys: `~/.mod/{cfg['name']}/env.json` (0600, off-tree)

Running it is owner-only: the CLI runs with its approval prompts off on this host's shell.
Everything agent-specific lives in `config.json` under `agent`; the Python and HTML are the
shared template in `orbit/agent/src/agenthub/template/`.
"""

    def remove(self, id: str, key=None, purge: bool = False) -> Dict[str, Any]:
        """Unwire an installed agent: stop it, drop the harness and persona.
        purge=True also deletes the module directory, and only one we made."""
        self._require_owner(key, "remove agent")
        with self._lock:
            inst = self._read("installed.json", {})
            rec = inst.pop(id, None)
            if not rec:
                raise KeyError(f"{id} is not installed")
            self._write("installed.json", inst)
        subprocess.run(["pm2", "delete", f"{rec['mod']}-api"], capture_output=True)
        if self.harness is not None:
            self.harness.RUNNERS.pop(rec.get("harness"), None)
            self.harness._loaded.pop(rec.get("harness"), None)
        out = {**rec, "removed": True}
        if self.agents is not None and id in self.agents.ls():
            try:
                self.agents.remove(id, key=key)
                out["persona_removed"] = True
            except Exception as e:
                out["persona_error"] = str(e)
        path = Path(rec["path"])
        if purge and path.is_dir() and self._ours(path):
            shutil.rmtree(path)
            out["purged"] = str(path)
        return out

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, q: str = "", **kwargs) -> Any:
        return self.search(q, k=int(kwargs.get("k", 20)))

    def test(self) -> bool:
        ids = [c["id"] for c in CATALOG + NATIVE]
        assert len(ids) == len(set(ids)), "duplicate catalog ids"
        for c in CATALOG:
            if c.get("run"):
                assert any("{query}" in a for a in c["run"]), c["id"]
        for f in TEMPLATE_FILES:
            assert (TEMPLATE / f).exists(), f
        r = self._lexical("terminal coding", {f"github:{c['id']}": {**c, "source": "github"}
                                              for c in CATALOG}, 5)
        assert r["results"], "lexical fallback found nothing"
        return True
