import json
import os
import socket
import subprocess
import time

import mod as m


class Mod:
    """caddy — automatic modc2.com routing through the mod protocol.

    Any module whose config.json declares ports gets a route at
    modc2.com/{name} (app) and modc2.com/api/{name} (API) — no hand-editing of
    /etc/caddy/Caddyfile. We generate a managed include (mod_routes.caddy) that
    the modc2.com site block imports, then reload Caddy.

    The generator is ADDITIVE and safe: modules already routed explicitly in the
    base Caddyfile are skipped (their hand-tuned blocks win), so this only adds
    routes for new modules that declare ports.

    Convention (per config.json):
      port      → API   (proxied at /api/{name}, prefix stripped)
      app_port  → app   (proxied at /{name}, prefix kept — Next basePath)
    """

    description = "Automatic modc2.com routing for mod modules (generates a Caddy include from config.json ports)."
    path = r'/root/mod/mod/orbit/caddy'

    CADDYFILE = os.environ.get("MOD_CADDYFILE", "/etc/caddy/Caddyfile")
    INCLUDE = "mod_routes.caddy"   # relative to the Caddyfile's dir
    SITE = os.environ.get("MOD_SITE", "modc2.com")
    ADMIN = os.environ.get("MOD_CADDY_ADMIN", "localhost:2099")
    HOST = os.environ.get("PM2_HOST", "localhost")

    # Roots scanned for routable modules.
    ROOTS = ["/root/mod/mod/orbit", "/root/mod/mod/core"]

    # ── mod protocol ─────────────────────────────────────────────────────────
    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': 'caddy',
            'description': self.description,
            'path': self.path,
            'caddyfile': self.CADDYFILE,
            'site': self.SITE,
            'fns': ['discover', 'routes', 'generate', 'apply', 'reload'],
        }

    def readme(self):
        for name in ['README.md', 'readme.md']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    # ── discovery ────────────────────────────────────────────────────────────
    @staticmethod
    def _port_live(port, host="127.0.0.1", timeout=0.25):
        """True if something is listening on the port — guards against the
        copy-pasted placeholder ports many dormant modules share."""
        if not port:
            return False
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def discover(self, live_only=True, require_optin=True):
        """Modules to auto-route, from their config.json.

        A module is routed only if it OPTS IN with `"route": true` in its
        config.json — declared intent. This is the safeguard against the
        copy-pasted placeholder ports many dormant modules carry (e.g. several
        modules nominally claim 50180/50120): without opt-in we'd hijack a port
        that's live but owned by a different service. `live_only` then drops any
        opted-in port that isn't actually listening, so we never add a dead
        upstream."""
        out = []
        for root in self.ROOTS:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                cfg_path = os.path.join(d, "config.json")
                if not os.path.isfile(cfg_path):
                    continue
                try:
                    cfg = json.load(open(cfg_path))
                except (json.JSONDecodeError, OSError):
                    continue
                if require_optin and not cfg.get("route"):
                    continue
                api = cfg.get("port") or (cfg.get("ports") or {}).get("api")
                app = cfg.get("app_port") or (cfg.get("ports") or {}).get("app")
                if not api and not app:
                    continue
                if live_only:
                    # Keep only the ports that are actually serving.
                    api = api if self._port_live(api) else None
                    app = app if self._port_live(app) else None
                    if not api and not app:
                        continue
                out.append({
                    "name": cfg.get("name", name),
                    "dir": d,
                    "api_port": api,
                    "app_port": app,
                })
        return out

    def _base_text(self):
        try:
            return open(self.CADDYFILE).read()
        except OSError:
            return ""

    def routes(self):
        """What WOULD be routed, split into new (auto) vs already-in-base."""
        base = self._base_text()
        new, existing = [], []
        for mod in self.discover():
            n = mod["name"]
            if f"/{n} " in base or f"@{n}_app" in base or f"path /{n}" in base:
                existing.append(n)
            else:
                new.append(mod)
        return {"new": new, "already_routed": existing,
                "site": self.SITE, "include": self.INCLUDE}

    # ── generation ───────────────────────────────────────────────────────────
    def generate(self):
        """Build the managed include text for all NEW (not-yet-routed) modules."""
        info = self.routes()
        lines = [
            "# ─────────────────────────────────────────────────────────────",
            "# AUTO-GENERATED by `m caddy/apply` — DO NOT EDIT BY HAND.",
            "# Routes every mod module that declares ports in its config.json.",
            f"# generated_at_epoch={int(time.time())}",
            "# ─────────────────────────────────────────────────────────────",
        ]
        for mod in info["new"]:
            n = mod["name"]
            if mod["api_port"]:
                lines += [
                    f"@{n}_api path /api/{n} /api/{n}/*",
                    f"handle @{n}_api {{",
                    f"    uri strip_prefix /api/{n}",
                    f"    reverse_proxy {self.HOST}:{mod['api_port']}",
                    "}",
                ]
            if mod["app_port"]:
                lines += [
                    f"@{n}_app path /{n} /{n}/*",
                    f"handle @{n}_app {{",
                    f"    reverse_proxy {self.HOST}:{mod['app_port']}",
                    "}",
                ]
        return "\n".join(lines) + "\n"

    # ── apply ────────────────────────────────────────────────────────────────
    def apply(self, reload=True):
        """Write the include, ensure the site block imports it, reload Caddy."""
        caddy_dir = os.path.dirname(self.CADDYFILE)
        include_path = os.path.join(caddy_dir, self.INCLUDE)
        text = self.generate()
        routed = self.routes()

        # 1. write managed include
        with open(include_path, "w") as f:
            f.write(text)

        # 2. ensure `import mod_routes.caddy` inside the site block
        base = self._base_text()
        added_import = False
        if f"import {self.INCLUDE}" not in base:
            # back up first
            bak = f"{self.CADDYFILE}.bak.{int(time.time())}"
            with open(bak, "w") as f:
                f.write(base)
            marker = f"{self.SITE} {{"
            idx = base.find(marker)
            if idx == -1:
                return {"error": f"site block '{self.SITE} {{' not found in {self.CADDYFILE}"}
            insert_at = idx + len(marker)
            base = base[:insert_at] + f"\n    import {self.INCLUDE}" + base[insert_at:]
            with open(self.CADDYFILE, "w") as f:
                f.write(base)
            added_import = True

        result = {
            "include_path": include_path,
            "import_added": added_import,
            "new_routes": [mr["name"] for mr in routed["new"]],
            "already_routed": routed["already_routed"],
        }
        if reload:
            result["reload"] = self.reload()
        return result

    def reload(self):
        """Reload Caddy from the base Caddyfile via the admin API."""
        try:
            r = subprocess.run(
                ["caddy", "reload", "--config", self.CADDYFILE,
                 "--adapter", "caddyfile", "--address", self.ADMIN],
                cwd=os.path.dirname(self.CADDYFILE),
                capture_output=True, text=True, timeout=60,
            )
            ok = r.returncode == 0
            return {"ok": ok, "stderr": r.stderr.strip()[-500:] if not ok else ""}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
