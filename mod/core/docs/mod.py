"""docs — the documentation hub for the mod protocol (core module).

Serves the protocol documentation pages shipped under `docs/` (getting-started,
cli, api, orbit, storage, keys, servers, skills, contracts, the whitepaper, …)
AND aggregates per-module docs (README.md / skill.md / config.json descriptions)
across the orbit, so there is one place to read "what mod is" and "what each
module does". A companion web app (`app/`) renders these pages at /docs.

CLI (via `m`):
    m docs/overview                 # the protocol overview (docs/README.md)
    m docs/pages                    # the doc pages
    m docs/page cli                 # read one page
    m docs/modules [group=orbit|core|all]
    m docs/doc claude               # a module's README + skill
    m docs/whitepaper [fmt=md|tex|simple]
    m docs/search auth
"""
import os
import json
import mod as m

HOME = os.path.expanduser("~")
REPO = os.environ.get("MOD_REPO", os.path.join(HOME, "mod", "mod"))


class Mod:
    description = "Documentation hub: protocol doc pages + aggregated module docs + the whitepaper"
    path = os.path.dirname(os.path.abspath(__file__))

    @property
    def docs_dir(self):
        return os.path.join(self.path, "docs")

    def forward(self, **kwargs):
        return self.overview()

    # ── protocol doc pages ───────────────────────────────────────────────
    # Every page docs/<name>.md may have a plain-language twin docs/simple/<name>.md.
    def pages(self) -> list:
        d = self.docs_dir
        if not os.path.isdir(d):
            return []
        return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md"))

    def page(self, name: str, simple: bool = False):
        fn = name if name.endswith(".md") else name + ".md"
        if simple:
            sp = os.path.join(self.docs_dir, "simple", fn)
            if os.path.exists(sp):
                return m.get_text(sp)
        p = os.path.join(self.docs_dir, fn)
        if not os.path.exists(p):
            raise FileNotFoundError(f"no doc page '{name}'. available: {self.pages()}")
        return m.get_text(p)

    def simple_pages(self) -> list:
        d = os.path.join(self.docs_dir, "simple")
        if not os.path.isdir(d):
            return []
        return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md"))

    def overview(self):
        for cand in ("README.md", "getting-started.md"):
            p = os.path.join(self.docs_dir, cand)
            if os.path.exists(p):
                return m.get_text(p)
        return self.description

    readme = overview

    # ── module catalog ───────────────────────────────────────────────────
    def _groups(self, group="all"):
        return ["orbit", "core"] if group == "all" else [group]

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
                    "description": self._desc(d, name),
                    "readme": os.path.exists(os.path.join(d, "README.md")),
                    "skill": os.path.exists(os.path.join(d, "skill.md")),
                })
        return out

    def _desc(self, d, name) -> str:
        for cfg in (os.path.join(d, "config.json"), os.path.join(d, name, "config.json")):
            if os.path.exists(cfg):
                try:
                    return json.loads(open(cfg).read()).get("description", "") or ""
                except Exception:
                    pass
        return ""

    def _module_dir(self, module):
        for g in ("orbit", "core"):
            d = os.path.join(REPO, g, module)
            if os.path.isdir(d):
                return d
        raise FileNotFoundError(f"module '{module}' not found")

    def doc(self, module: str) -> dict:
        d = self._module_dir(module)

        def read(fn):
            p = os.path.join(d, fn)
            return m.get_text(p) if os.path.exists(p) else None

        return {"module": module, "description": self._desc(d, module),
                "readme": read("README.md"), "skill": read("skill.md")}

    # ── whitepaper ───────────────────────────────────────────────────────
    def whitepaper(self, fmt: str = "md"):
        fn = {"tex": "whitepaper.tex", "simple": os.path.join("simple", "whitepaper.md")}.get(fmt, "whitepaper.md")
        p = os.path.join(self.docs_dir, fn)
        return m.get_text(p) if os.path.exists(p) else None

    # ── search ───────────────────────────────────────────────────────────
    def search(self, query: str) -> dict:
        q = query.lower()
        page_hits = []
        for pg in self.pages():
            try:
                if q in pg.lower() or q in self.page(pg).lower():
                    page_hits.append(pg)
            except Exception:
                pass
        mod_hits = [x["name"] for x in self.modules("all")
                    if q in (x["name"] + " " + x["description"]).lower()]
        return {"pages": page_hits, "modules": mod_hits}

    def info(self):
        mods = self.modules("all")
        return {
            "name": "docs", "description": self.description, "path": self.path,
            "pages": self.pages(),
            "simple_pages": self.simple_pages(),
            "modules_documented": len(mods),
            "with_readme": sum(1 for x in mods if x["readme"]),
            "with_skill": sum(1 for x in mods if x["skill"]),
        }
