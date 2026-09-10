"""hub — the module catalog for the orbit (core module).

One job: know what modules exist and what each one is. It walks the repo tree
(orbit/ + core/) and reports every module's name, group, description, and the
docs it ships (README.md / skill.md). Nothing here knows about protocol doc
pages or the whitepaper — that's `docs`, which depends on this.

The catalog is also a loopback service (api.py, :50520) copied from
orbit/build's HUB data plane — module rows key-for-key with build's
GET /modules, app screenshots, a batch port probe, and the autosnap CID
loop. See README.md; start with `bash start.sh`.

CLI (via `m`):
    m hub/modules [group=orbit|core|all]
    m hub/names orbit
    m hub/doc claude               # a module's README + skill
    m hub/desc claude
    m hub/dir claude
    m hub/search auth
    m hub/probe 8890,8893          # which local ports answer
    m hub/screenshot claude        # cached PNG path for a module's app
    m hub/snapshot_status          # how the autosnap loop is doing
"""
import os
import json
import mod as m

HOME = os.path.expanduser("~")
REPO = os.environ.get("MOD_REPO", os.path.join(HOME, "mod", "mod"))
API_URL = os.environ.get("HUB_API_URL", "http://127.0.0.1:50520")

GROUPS = ("orbit", "core")


class Mod:
    description = "Module catalog: every module in the orbit, with its description and docs"
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        return self.modules(**kwargs)

    # ── catalog ──────────────────────────────────────────────────────────
    def _groups(self, group="all"):
        return list(GROUPS) if group == "all" else [group]

    def modules(self, group="all") -> list:
        out = []
        for g in self._groups(group):
            base = os.path.join(REPO, g)
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                d = os.path.join(base, name)
                if not os.path.isdir(d) or name.startswith((".", "_")):
                    continue
                out.append({
                    "name": name, "group": g,
                    "description": self.desc(name, _dir=d),
                    "readme": os.path.exists(os.path.join(d, "README.md")),
                    "skill": os.path.exists(os.path.join(d, "skill.md")),
                })
        return out

    def names(self, group="all") -> list:
        return [x["name"] for x in self.modules(group)]

    def dir(self, module: str) -> str:
        for g in GROUPS:
            d = os.path.join(REPO, g, module)
            if os.path.isdir(d):
                return d
        raise FileNotFoundError(f"module '{module}' not found")

    # A module's config.json may sit at <mod>/config.json or <mod>/<name>/config.json.
    def desc(self, module: str, _dir=None) -> str:
        d = _dir or self.dir(module)
        for cfg in (os.path.join(d, "config.json"), os.path.join(d, module, "config.json")):
            if os.path.exists(cfg):
                try:
                    return json.loads(open(cfg).read()).get("description", "") or ""
                except Exception:
                    pass
        return ""

    def doc(self, module: str) -> dict:
        d = self.dir(module)

        def read(fn):
            p = os.path.join(d, fn)
            return m.get_text(p) if os.path.exists(p) else None

        return {"module": module, "description": self.desc(module, _dir=d),
                "readme": read("README.md"), "skill": read("skill.md")}

    def search(self, query: str) -> list:
        q = query.lower()
        return [x["name"] for x in self.modules("all")
                if q in (x["name"] + " " + x["description"]).lower()]

    # ── service (api.py :50520) ──────────────────────────────────────────
    def probe(self, ports) -> dict:
        """Which local ports answer — direct socket check, no api needed."""
        import socket
        out = {}
        for p in str(ports).split(","):
            p = p.strip()
            if not p.isdigit():
                continue
            port = int(p)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    out[port] = True
            except OSError:
                out[port] = False
        return out

    def screenshot(self, module: str, refresh: bool = False) -> str:
        """Capture (via the hub api) and return the cached PNG path."""
        import urllib.request
        q = "?refresh=1" if refresh else ""
        with urllib.request.urlopen(f"{API_URL}/modules/{module}/screenshot{q}",
                                    timeout=90) as resp:
            resp.read()
        return os.path.join(HOME, ".mod", "hub", "screenshots", f"{module}.png")

    def snapshot_status(self) -> dict:
        import urllib.request
        with urllib.request.urlopen(f"{API_URL}/autosnap/status", timeout=10) as resp:
            return json.load(resp)

    def info(self):
        mods = self.modules("all")
        return {
            "name": "hub", "description": self.description, "path": self.path,
            "repo": REPO,
            "modules": len(mods),
            "by_group": {g: sum(1 for x in mods if x["group"] == g) for g in GROUPS},
            "with_readme": sum(1 for x in mods if x["readme"]),
            "with_skill": sum(1 for x in mods if x["skill"]),
        }
