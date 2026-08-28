"""docs — the documentation hub for the mod protocol (core module).

Serves the protocol documentation pages shipped under `docs/` (getting-started,
cli, api, orbit, storage, keys, servers, skills, contracts, the whitepaper, …),
so there is one place to read "what mod is". The per-module catalog ("what does
each module do") lives in its own module, `hub`, which this one depends on and
re-exports. A companion web app (`app/`) renders these pages at /docs.

CLI (via `m`):
    m docs/overview                 # the protocol overview (docs/README.md)
    m docs/pages                    # the doc pages
    m docs/page cli                 # read one page
    m docs/modules [group=orbit|core|all]   # → hub
    m docs/doc claude               # a module's README + skill → hub
    m docs/whitepaper [fmt=md|tex|simple]
    m docs/search auth
    m docs/mcp                      # how to connect an agent to the doc tools
"""
import os
import mod as m


class Mod:
    description = "Documentation hub: protocol doc pages + the whitepaper, plus the module catalog from hub"
    path = os.path.dirname(os.path.abspath(__file__))

    @property
    def docs_dir(self):
        return os.path.join(self.path, "docs")

    @property
    def hub(self):
        return m.mod("hub")()

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

    # ── module catalog (lives in `hub`; re-exported so the CLI stays one place)
    def modules(self, group="all") -> list:
        return self.hub.modules(group)

    def doc(self, module: str) -> dict:
        return self.hub.doc(module)

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
        return {"pages": page_hits, "modules": self.hub.search(query)}

    # ── mcp ──────────────────────────────────────────────────────────────
    # The same functions above, served to agents as MCP tools (api/mcp.py).
    def mcp(self) -> dict:
        cfg = m.get_json(os.path.join(self.path, "config.json"))
        script = os.path.join(self.path, "api", "mcp.py")
        port, base = cfg.get("mcp_port", 50192), cfg.get("base_path", "/docs")
        return {
            "tools": ["docs_overview", "docs_pages", "docs_page", "docs_search",
                      "docs_whitepaper", "docs_modules", "docs_module_doc"],
            "stdio": f"python3 {script}",
            "http": f"http://localhost:{cfg.get('app_port', 50191)}{base}/mcp",
            "http_direct": f"http://localhost:{port}/mcp",
            "claude_code": f"claude mcp add docs -- python3 {script}",
            "serve": "m pm/start docs target=api   # or: bash api/start.sh",
        }

    def info(self):
        return {
            "name": "docs", "description": self.description, "path": self.path,
            "mcp": self.mcp(),
            "pages": self.pages(),
            "simple_pages": self.simple_pages(),
            "deps": {"hub": self.hub.info()},
        }
