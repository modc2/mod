"""hub — the module catalog for the orbit (core module).

One job: know what modules exist and what each one is. It walks the repo tree
(orbit/ + core/) and reports every module's name, group, description, and the
docs it ships (README.md / skill.md). Nothing here knows about protocol doc
pages or the whitepaper — that's `docs`, which depends on this.

CLI (via `m`):
    m hub/modules [group=orbit|core|all]
    m hub/names orbit
    m hub/doc claude               # a module's README + skill
    m hub/desc claude
    m hub/dir claude
    m hub/search auth
"""
import os
import json
import mod as m

HOME = os.path.expanduser("~")
REPO = os.environ.get("MOD_REPO", os.path.join(HOME, "mod", "mod"))

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
