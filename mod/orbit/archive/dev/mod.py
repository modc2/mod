"""
dev — a modular, OpenAI-API-standard multi-provider LLM gateway.

A single console over every OpenAI-compatible model provider. Each provider
(venice, openrouter, codex/OpenAI, claude, …) is one instance of the same
template — an OpenAI-compatible base URL exposing ``/models`` and
``/chat/completions``. The owner can add new providers at runtime with no code
change. Requests resolve a key in order: the caller's own BYOK key (encrypted
at rest) → the provider's backend key from the environment → a "no key" error.

Stack (mirrors the venice module):
  - Rust (axum) gateway  → src/api,  binary ``dev-api``, port 8870
  - Next.js app          → src/app,  port 8871, served under /dev
  - run by pm2 via ecosystem.config.js (./start.sh builds + launches)

This Python module is a thin manager: build/serve the two processes, register
the app with the mod gateway, and expose health/status/providers for the CLI.
Provider config + BYOK keys live off-chain (config.json seed + ~/.mod/dev).
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import mod as m


class Mod:
    description = (
        "Modular OpenAI-standard multi-provider LLM gateway — Rust backend + "
        "Next.js app. venice, openrouter, openai, claude, and any you add. "
        "BYOK or backend key, SSE streaming, wallet (mod protocol) auth."
    )

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.port = int(self.config.get("port", 8870))
        self.app_port = int(self.config.get("app_port", 8871))
        self.owner = self.config.get("owner", "")

    def _load_config(self):
        p = self.module_dir / "config.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}

    # ━━ Health / status / providers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def health(self):
        return {"status": "ok", "module": "dev", "providers": list(self.providers().keys())}

    def providers(self) -> dict:
        """The seeded provider templates from config.json (no secrets)."""
        return self.config.get("providers", {})

    def status(self):
        """pm2 process status for dev-api / dev-app."""
        out = {}
        for name in ("dev-api", "dev-app"):
            r = subprocess.run(["pm2", "jlist"], capture_output=True, text=True)
            out["pm2_query_ok"] = r.returncode == 0
        try:
            procs = json.loads(subprocess.run(["pm2", "jlist"], capture_output=True, text=True).stdout or "[]")
            running = {p.get("name"): p.get("pm2_env", {}).get("status") for p in procs}
        except Exception:
            running = {}
        return {
            "module": "dev",
            "api": {"port": self.port, "pm2": "dev-api", "status": running.get("dev-api", "stopped")},
            "app": {"port": self.app_port, "pm2": "dev-app", "status": running.get("dev-app", "stopped")},
            "providers": list(self.providers().keys()),
        }

    # ━━ Build + serve ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def build(self):
        """Build the Rust gateway and install app deps (idempotent)."""
        api_dir = self.module_dir / "src" / "api"
        app_dir = self.module_dir / "src" / "app"
        r = subprocess.run(["cargo", "build", "--release"], cwd=str(api_dir))
        if r.returncode != 0:
            return {"ok": False, "stage": "cargo build"}
        if not (app_dir / "node_modules").exists():
            subprocess.run(["npm", "install"], cwd=str(app_dir))
        return {"ok": True}

    def serve(self, mode: str = "dev", register: bool = True):
        """Build, then (re)start both processes under pm2 via ecosystem.config.js.

        mode: "dev" (next dev, hot reload) or "prod" (next build + start).
        Re-run any time to pick up code changes. Registers with the gateway.
        """
        built = self.build()
        if not built.get("ok"):
            return {"ok": False, "error": "build failed", "detail": built}

        env = {**os.environ, "DEV_MODE": mode}
        if mode == "prod":
            app_dir = self.module_dir / "src" / "app"
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(app_dir),
                env={**env, "NEXT_PUBLIC_BASE_PATH": "/dev"},
            )
        eco = str(self.module_dir / "ecosystem.config.js")
        subprocess.run(["pm2", "start", eco, "--update-env"], env=env)
        subprocess.run(["pm2", "save"], capture_output=True, text=True)

        result = {
            "ok": True,
            "mode": mode,
            "api": f"http://localhost:{self.port}",
            "app": f"http://localhost:{self.app_port}/dev",
            "pm2": ["dev-api", "dev-app"],
        }
        if register:
            result["registration"] = self.register()
        return result

    def serve_api(self):
        """Start only the Rust gateway under pm2."""
        self.build()
        eco = str(self.module_dir / "ecosystem.config.js")
        subprocess.run(["pm2", "start", eco, "--only", "dev-api", "--update-env"])
        return {"api": f"http://localhost:{self.port}", "pm2": "dev-api"}

    def serve_app(self, mode: str = "dev"):
        """Start only the Next.js app under pm2."""
        eco = str(self.module_dir / "ecosystem.config.js")
        subprocess.run(["pm2", "start", eco, "--only", "dev-app", "--update-env"],
                       env={**os.environ, "DEV_MODE": mode})
        return {"app": f"http://localhost:{self.app_port}/dev", "pm2": "dev-app"}

    def kill(self):
        killed = []
        for name in ("dev-api", "dev-app"):
            if subprocess.run(["pm2", "delete", name], capture_output=True, text=True).returncode == 0:
                killed.append(name)
        return {"killed": killed}

    # ━━ Gateway registration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def register(self, app_url=None, api_url=None, owner=None, gateway="https://modc2.com"):
        app_url = app_url or f"http://localhost:{self.app_port}"
        api_url = api_url or f"http://localhost:{self.port}"
        try:
            ns = m.mod("server.namespace")()
            ns.reg("dev", app_url)
            ns.reg_app("dev", app_url, owner=owner or self.owner or "",
                       port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/dev"
            print(f"dev registered → {public}  (app: {app_url}, api: {api_url})")
            return {"ok": True, "gateway": public, "app": app_url, "api": api_url}
        except Exception as e:
            print(f"dev: gateway registration failed: {e}")
            return {"ok": False, "error": str(e), "app": app_url, "api": api_url}

    def deregister(self):
        try:
            m.mod("server.namespace")().dereg_app("dev")
            return {"ok": True, "deregistered": "dev"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def forward(self, action: Optional[str] = None, **kwargs):
        """CLI entry: dev <action> [args]"""
        actions = {
            "health": lambda: self.health(),
            "status": lambda: self.status(),
            "providers": lambda: self.providers(),
            "build": lambda: self.build(),
            "serve": lambda: self.serve(mode=kwargs.get("mode", "dev"),
                                        register=kwargs.get("register", True)),
            "serve_api": lambda: self.serve_api(),
            "serve_app": lambda: self.serve_app(mode=kwargs.get("mode", "dev")),
            "kill": lambda: self.kill(),
            "register": lambda: self.register(
                app_url=kwargs.get("app_url"), api_url=kwargs.get("api_url"),
                owner=kwargs.get("owner"), gateway=kwargs.get("gateway", "https://modc2.com")),
            "deregister": lambda: self.deregister(),
        }
        if not action or action not in actions:
            return {
                "module": "dev",
                "description": self.description,
                "actions": list(actions.keys()),
                "status": self.status(),
            }
        return actions[action]()
