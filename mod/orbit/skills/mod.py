"""skills — the agent skill marketplace.

A tool is something an agent can call. A skill is something it can *learn*: a
SKILL.md — front matter plus instructions — that tells a model when to reach
for the tools it already has and exactly how to use them for one job. The
fleet had no place to find those, so this module is that place.

It scrapes the open web for them:

    anthropic  the official anthropics/skills catalog
    topics     repos that tag themselves claude-skill / agent-skill
    code       GitHub code search for SKILL.md — the widest net (needs a token)
    github     repo search over names and descriptions
    awesome    curated community indexes, parsed out of their READMEs
    registry   every module on this host, whose skill.md is already this format

One query fans out to all six at once, duplicates across sources merge onto
one card, and the ranking is source trust × relevance × a capped nod to stars,
so a 40k-star framework that mentions your query cannot outrank the skill that
answers it.

    m skills                                  # what this is
    m skills/search pdf                       # scan the web
    m skills/get gh:anthropics/skills:skills/pdf
    m skills/install gh:anthropics/skills:skills/pdf
    m skills/install gh:anthropics/skills all=true    # the whole pack
    m skills/write name=deploy body=@checklist.md     # author one yourself
    m skills/installed                        # the catalog
    m skills/load names=pdf,deploy            # bodies, for a run
    m skills/serve                            # api, mcp and console on one port

NOTHING HERE IS EXECUTED
    A skill is markdown. Installing one writes a file; it never runs a script,
    never installs a package, never adds an executable tool. `tools:` in the
    front matter names tools the agent already has — a skill cannot grant a
    capability, only teach the use of one. That is what makes a marketplace of
    documents scraped off the internet a safe thing to have.

WHERE IT LIVES
    ~/.mod/skills/catalog/<name>/SKILL.md — one folder per skill, portable:
    copy it into ~/.claude/skills and Claude Code reads it; copy one out of
    there and this reads it. Same format, no converter.

READS ARE OPEN, THE CATALOG IS NOT
    Searching, reading and loading are open to anyone. The catalog is shared
    state on this box, so installing, writing and removing want a caller on
    this box or the bearer token in ~/.mod/skills/server.secret.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# appended, never prepended: this directory holds a mod.py that would shadow
# the protocol's own `mod` package for anything importing us afterwards
if HERE not in sys.path:
    sys.path.append(HERE)

from skillsrc import mcp as mcpsrv          # noqa: E402
from skillsrc.market import Market          # noqa: E402
from skillsrc.sources import SOURCE_IDS     # noqa: E402

PORT = int(os.environ.get("SKILLS_PORT", 50860))


class Skills:
    """A marketplace of agent skills, scraped off the open web."""
    description = __doc__.strip().splitlines()[0]

    def __init__(self, dir: str = None, **kwargs):
        self.market = Market(dir)

    # ── the market ───────────────────────────────────────────────────

    def search(self, q: str = "", sources=None, limit: int = 30, fresh: bool = False):
        """Scan every source at once for skills matching a query."""
        return self.market.search(q, sources, limit, fresh)

    def sources(self):
        """The sources a scan reaches, and which are ready."""
        return self.market.sources()

    def get(self, id: str, path: str = None):
        """One result, with the SKILL.md behind it."""
        return self.market.get(id, path)

    def install(self, id: str, path: str = None, name: str = None,
                all: bool = False, who: str = ""):
        """File a skill in this host's catalog. Writes markdown; runs nothing."""
        return self.market.install(id, path, name, all=all, who=who)

    def write(self, name: str, body: str, description: str = "",
              tools=None, tags=None, who: str = ""):
        """Author a skill here instead of finding one."""
        if body and body.startswith("@") and os.path.isfile(body[1:]):
            body = open(body[1:], encoding="utf-8").read()
        return self.market.write(name, body, description, tools, tags, who)

    def installed(self, q: str = "", tag: str = None):
        """The catalog — every skill on this host, as cards."""
        return self.market.installed(q, tag)

    def doc(self, name: str):
        """The markdown of one skill — what you put in front of a model."""
        return self.market.doc(name)

    def load(self, names=None, q: str = ""):
        """Several skills, bodies included — the call an agent makes at the
        start of a run to learn what it is allowed to do."""
        if isinstance(names, str):
            names = [n for n in names.replace(" ", "").split(",") if n]
        return self.market.publish(names, q)

    def remove(self, name: str):
        """Take a skill back out of the catalog."""
        return self.market.remove(name)

    def token(self, token: str = None):
        """Set or clear the GitHub token that unlocks code search."""
        return self.market.token(token)

    # ── the server ───────────────────────────────────────────────────

    def serve(self, port: int = None, bind: str = None, background: bool = True):
        """Start the API, the MCP server and the console on one port."""
        port = int(port or PORT)
        cmd = [sys.executable, "-m", "skillsrc.api", "--port", str(port)]
        env = {**os.environ, "SKILLS_PORT": str(port)}
        if bind:
            env["SKILLS_BIND"] = bind
        if not background:
            return subprocess.call(cmd, cwd=HERE, env=env)
        proc = subprocess.Popen(cmd, cwd=HERE, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"pid": proc.pid, "port": port,
                "url": f"http://127.0.0.1:{port}/skills",
                "mcp": f"http://127.0.0.1:{port}/mcp"}

    def kill(self, port: int = None):
        """Stop the server on a port."""
        port = int(port or PORT)
        out = subprocess.run(["bash", "-c", f"lsof -ti tcp:{port} | xargs -r kill"],
                             capture_output=True, text=True)
        return {"killed": port, "err": out.stderr.strip() or None}

    def health(self):
        """Liveness, catalog size, whether code search is unlocked."""
        return self.market.health()

    def mcp(self, request=None, **kwargs):
        """MCP JSON-RPC in, JSON-RPC out — the same ten operations."""
        if request is None:
            return mcpsrv.info()
        if isinstance(request, str):
            request = json.loads(request)
        return mcpsrv.rpc(request, authorized=True)

    def readme(self):
        return open(os.path.join(HERE, "README.md"), encoding="utf-8").read()

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, q: str = None, **kwargs):
        """
        forward()          -> what this module is
        forward('pdf')     -> a scan for 'pdf'
        forward(action=…)  -> any operation by name
        """
        action = kwargs.pop("action", None)
        if action:
            fn = getattr(self, action, None)
            if not callable(fn) or action.startswith("_"):
                raise KeyError(f"no such operation: {action}")
            return fn(**kwargs)
        if q:
            return self.search(q, **kwargs)
        return self.info()

    def info(self):
        return {
            "module": "skills",
            "what": ("A marketplace of agent skills. A skill is a SKILL.md — front "
                     "matter plus instructions — scraped off the open web, filed in a "
                     "catalog on this box, and handed to an agent as context."),
            "sources": SOURCE_IDS,
            "operations": ["search", "sources", "get", "install", "write", "installed",
                           "doc", "load", "remove", "token", "serve", "health", "mcp"],
            "safety": "documents, never code — installing a skill cannot run anything",
            "catalog": str(self.market.store.catalog),
            "url": f"http://127.0.0.1:{PORT}/skills",
            **self.market.store.stats(),
        }

    def test(self):
        """Offline test: the format, the catalog and the ranking.

        Deliberately does not touch the network — a test that fails because
        GitHub rate-limited is a test nobody trusts. The network paths are
        covered in tests/test_skills.py, which skips when offline.
        """
        import tempfile
        from skillsrc import skill as sd
        with tempfile.TemporaryDirectory() as tmp:
            mk = Market(tmp)
            md = ("---\nname: Demo Skill\ndescription: |-\n  Do the demo,\n  carefully\n"
                  "tools: [bash, read]\n---\n\n# Demo\n\nRun the demo when asked.\n")
            rec = sd.normalize(md, source="test")
            assert rec["name"] == "demo-skill", rec["name"]
            assert rec["description"].startswith("Do the demo"), rec["description"]
            assert rec["tools"] == ["bash", "read"]
            assert sd.looks_like_skill(md)
            # round trip: what we write back parses to the same skill
            again = sd.normalize(sd.to_markdown(rec))
            assert again["name"] == rec["name"] and again["tools"] == rec["tools"]
            # catalog
            w = mk.write("demo-skill", md, who="test")
            assert w["wrote"] == "demo-skill"
            assert mk.installed()["total"] == 1
            assert "Run the demo" in mk.doc("demo-skill")["markdown"]
            assert mk.publish(["demo-skill"])["skills"][0]["name"] == "demo-skill"
            # a name cannot escape the catalog
            try:
                mk.store.read("../../../etc/passwd")
                assert False, "path traversal was allowed"
            except (KeyError, ValueError):
                pass
            assert mk.remove("demo-skill")["removed"] == "demo-skill"
            assert mk.installed()["total"] == 0
            # ranking: the name matters more than the description
            hit = {"source": "anthropic", "name": "pdf", "description": "", "stars": 0}
            miss = {"source": "github", "name": "x", "description": "pdf", "stars": 0}
            assert mk.web.score(hit, "pdf") > mk.web.score(miss, "pdf")
            # mcp and rest cannot drift: every tool is a Market method
            for t in mcpsrv.TOOLS:
                assert t["name"].startswith("skills_")
        return {"passed": True, "checks": ["format", "roundtrip", "catalog", "traversal",
                                           "ranking", "mcp"]}


_default = None


def _mod() -> Skills:
    global _default
    if _default is None:
        _default = Skills()
    return _default


def forward(q: str = None, **kwargs):
    return _mod().forward(q, **kwargs)


def _op(name: str):
    """Module-level alias for a method, bound lazily so importing this file
    never builds a Market (and never touches ~/.mod) on its own."""
    def call(*a, **k):
        return getattr(_mod(), name)(*a, **k)
    call.__name__ = name
    call.__doc__ = getattr(Skills, name).__doc__
    return call


for _name in ("search", "sources", "get", "install", "write", "installed", "doc",
              "load", "remove", "token", "serve", "kill", "health", "mcp", "info",
              "readme", "test"):
    globals()[_name] = _op(_name)

if __name__ == "__main__":
    args = sys.argv[1:]
    op = args[0] if args else "info"
    kw = {}
    pos = []
    for a in args[1:]:
        if "=" in a and not a.startswith("http"):
            k, _, v = a.partition("=")
            kw[k] = {"true": True, "false": False}.get(v.lower(), v)
        else:
            pos.append(a)
    print(json.dumps(getattr(_mod(), op)(*pos, **kw), indent=2, default=str))
