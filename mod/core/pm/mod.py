"""Core process manager (`pm`).

Supervises module processes and standardizes their ENVIRONMENT on nix: every
service is launched inside its "nix image" — the module's own `flake.nix` if it
ships one, otherwise the shared `core/nix` env (`modenv`). The supervisor
backend is pm2 by default and is swappable ("or a better one").

The nix image is imported with `nix print-dev-env` and the real server is then
`exec`'d, so pm2 tracks the actual process (no lingering `nix develop` parent).

CLI (via the `m` dispatcher):
    m pm/start <module> [target=api|app]
    m pm/stop <module>            m pm/restart <module>
    m pm/ps [module]              m pm/logs <module>
    m pm/image_info <module>      # what image + services would be used
"""

import os
import json
import shutil
import subprocess
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
REPO = Path(os.environ.get("MOD_REPO", str(HOME / "mod" / "mod")))
SHARED_NIX = REPO / "core" / "nix"
RUN_DIR = HOME / ".mod" / "pm"


class Pm:
    backend = "pm2"

    # ── module resolution ────────────────────────────────────────────────
    def dir(self, module: str) -> Path:
        for grp in ("orbit", "core"):
            d = REPO / grp / module
            if d.is_dir():
                return d
        raise FileNotFoundError(f"module '{module}' not found under {REPO}/{{orbit,core}}")

    def config(self, module: str) -> dict:
        d = self.dir(module)
        for p in (d / "config.json", d / module / "config.json"):
            if p.exists():
                try:
                    return json.loads(p.read_text())
                except Exception:
                    pass
        return {}

    # ── nix image ────────────────────────────────────────────────────────
    def has_nix(self) -> bool:
        return shutil.which("nix") is not None

    def image(self, module: str):
        """Flake ref for the module's nix image: its own flake.nix if present,
        else the shared core/nix. None when nix isn't available (run bare)."""
        if not self.has_nix():
            return None
        d = self.dir(module)
        if (d / "flake.nix").exists():
            return f"path:{d}"
        if (SHARED_NIX / "flake.nix").exists():
            return f"path:{SHARED_NIX}"
        return None

    # ── service discovery ────────────────────────────────────────────────
    def services(self, module: str):
        """[(svc, cwd, cmd)] for api/app from conventional start scripts; falls
        back to a single top-level start.sh ('main')."""
        d = self.dir(module)
        out = []
        for svc, cands in (("api", [d / "src" / "api", d / "api"]),
                           ("app", [d / "src" / "app", d / "app"])):
            for c in cands:
                if (c / "start.sh").exists():
                    out.append((svc, c, "bash start.sh"))
                    break
        if not out and (d / "start.sh").exists():
            out.append(("main", d, "bash start.sh"))
        return out

    # ── nix-wrapped launch wrapper ───────────────────────────────────────
    def _wrapper(self, module: str, svc: str, cwd: Path, cmd: str) -> Path:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        img = self.image(module)
        wp = RUN_DIR / f"{module}.{svc}.sh"
        lines = ["#!/usr/bin/env bash", "set -e", f'cd "{cwd}"']
        if img:
            # Import the nix image's environment into THIS shell, then exec the
            # server so pm2 tracks the real process directly.
            lines.append(f'eval "$(nix print-dev-env {img})"')
        lines.append(f"exec {cmd}")
        wp.write_text("\n".join(lines) + "\n")
        wp.chmod(0o755)
        return wp

    # ── pm2 backend ──────────────────────────────────────────────────────
    def _pm2(self, *args):
        return subprocess.run(["pm2", *args], capture_output=True, text=True)

    def start(self, module: str, target: str = None):
        svcs = self.services(module)
        if target:
            svcs = [s for s in svcs if s[0] == target]
        if not svcs:
            return {"error": f"no services (api/app/main start.sh) found for {module}"}
        started = []
        for svc, cwd, cmd in svcs:
            wp = self._wrapper(module, svc, cwd, cmd)
            name = f"{module}.{svc}"
            self._pm2("delete", name)
            r = self._pm2("start", str(wp), "--name", name)
            started.append({"name": name, "ok": r.returncode == 0,
                            "image": self.image(module), "wrapper": str(wp)})
        self._pm2("save")
        return {"module": module, "backend": self.backend, "started": started}

    def stop(self, module: str, target: str = None):
        return self._act("stop", module, target)

    def restart(self, module: str, target: str = None):
        return self._act("restart", module, target)

    def _act(self, verb: str, module: str, target):
        names = [f"{module}.{s[0]}" for s in self.services(module)
                 if (target is None or s[0] == target)]
        results = [{"name": n, "ok": self._pm2(verb, n).returncode == 0} for n in names]
        return {"module": module, "action": verb, "results": results}

    def ps(self, module: str = None):
        r = self._pm2("jlist")
        try:
            arr = json.loads(r.stdout)
        except Exception:
            return {"error": "pm2 jlist failed"}
        out = []
        for p in arr:
            nm = p.get("name", "")
            if module and not nm.startswith(module + "."):
                continue
            e = p.get("pm2_env", {})
            out.append({"name": nm, "status": e.get("status"), "pid": p.get("pid"),
                        "restarts": e.get("restart_time")})
        return out

    status = ps

    def logs(self, module: str, lines: int = 40):
        out = {}
        for s in self.services(module):
            n = f"{module}.{s[0]}"
            out[n] = self._pm2("logs", n, "--lines", str(lines), "--nostream").stdout[-4000:]
        return out

    def image_info(self, module: str):
        return {"module": module, "nix_available": self.has_nix(),
                "image": self.image(module),
                "services": [s[0] for s in self.services(module)],
                "shared_env": str(SHARED_NIX)}
