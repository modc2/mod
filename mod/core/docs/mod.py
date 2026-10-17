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
    m docs/ask "how do skills work?"   # chatbot: RAG over the docs, answered by a Liquid LFM
    m docs/mcp                      # how to connect an agent to the doc tools
"""
import os
import re
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

    # ── chatbot (RAG over the docs, answered by a Liquid LFM) ────────────
    # Retrieval is dependency-free keyword ranking over the same pages the app
    # renders; generation is the resident LFM in the `liquidai` module, called
    # in-process (a shell here is the operator, so its owner token self-mints —
    # no HTTP auth). One method, wrapped as the `docs_ask` MCP tool and the web
    # chat panel, so CLI, app and tool can't drift. See [[liquidai-module-notes]].
    _STOP = {"the", "and", "for", "how", "what", "does", "can", "you", "are",
             "with", "from", "this", "that", "was", "were", "has", "have",
             "module", "modules", "protocol", "mod", "about", "into", "when",
             "why", "who", "which", "your", "there", "their"}

    @property
    def _chat_cfg(self) -> dict:
        cfg = (m.get_json(os.path.join(self.path, "config.json")) or {}).get("chat", {})
        return {
            "model": cfg.get("model", "LiquidAI/LFM2.5-1.2B-Instruct"),
            "runtime": cfg.get("runtime", "server"),
            "max_tokens": int(cfg.get("max_tokens", 512)),
            "max_context_chars": int(cfg.get("max_context_chars", 9000)),
            "top_pages": int(cfg.get("top_pages", 3)),
        }

    def _rank_pages(self, question: str, limit: int) -> list:
        """Top `limit` doc pages for a question, by keyword frequency + a name-hit
        boost. Naive but fast and offline — good enough to feed the model context."""
        words = {w for w in re.findall(r"[a-z0-9]+", question.lower())
                 if len(w) > 2 and w not in self._STOP} or {question.lower().strip()}
        scored = []
        for pg in self.pages():
            try:
                body = self.page(pg).lower()
            except Exception:
                continue
            score = sum(body.count(w) for w in words) + 5 * sum(w in pg.lower() for w in words)
            if score:
                scored.append((score, pg))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [pg for _, pg in scored[:limit]]

    def ask(self, question: str, model: str = None, runtime: str = None,
            max_tokens: int = None) -> dict:
        """Answer a natural-language question about the mod protocol, grounded in
        the doc pages and generated by a Liquid LFM. Returns the answer, the pages
        used as sources, and the model. Never raises on a downstream outage —
        returns ok:false with the sources it would have cited."""
        q = (question or "").strip()
        if not q:
            raise ValueError("question required")
        cfg = self._chat_cfg
        pages = self._rank_pages(q, cfg["top_pages"])
        budget, used, parts = cfg["max_context_chars"], 0, []
        for pg in pages:
            chunk = self.page(pg)[: max(0, budget - used)]
            if not chunk:
                break
            parts.append(f"## Page: {pg}\n{chunk}")
            used += len(chunk)
        context = "\n\n".join(parts) or "(no matching documentation pages)"
        system = (
            "You are the assistant for the mod protocol documentation. Answer the "
            "user's question using ONLY the documentation context below. Be concise "
            "and technical. Name the page(s) you drew from. If the answer is not in "
            "the context, say so plainly and suggest which page to read.\n\n"
            "=== DOCUMENTATION CONTEXT ===\n" + context)
        try:
            r = m.mod("liquidai")().chat(
                prompt=q, system=system, model=model or cfg["model"],
                runtime=runtime or cfg["runtime"], max_tokens=max_tokens or cfg["max_tokens"])
        except Exception as e:
            return {"ok": False, "answer": None, "sources": pages,
                    "error": f"liquidai unreachable: {type(e).__name__}: {e}"}
        if not r.get("ok"):
            return {"ok": False, "answer": r.get("text") or None, "sources": pages,
                    "error": r.get("error") or "generation failed"}
        return {"ok": True, "answer": (r.get("text") or "").strip(),
                "sources": pages, "model": model or cfg["model"]}

    # ── mcp ──────────────────────────────────────────────────────────────
    # The same functions above, served to agents as MCP tools (api/mcp.py).
    def mcp(self) -> dict:
        cfg = m.get_json(os.path.join(self.path, "config.json"))
        script = os.path.join(self.path, "api", "mcp.py")
        port, base = cfg.get("mcp_port", 50192), cfg.get("base_path", "/docs")
        return {
            "tools": ["docs_overview", "docs_pages", "docs_page", "docs_search",
                      "docs_whitepaper", "docs_modules", "docs_module_doc", "docs_ask"],
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
