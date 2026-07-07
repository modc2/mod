import json
import os
import re
import socket
import subprocess
import time

import mod as m


class Mod:
    """caddy — THE router: one generated site block for {host}/{mod}.

    Every module whose config.json opts in (`"route": true`) and declares ports
    gets a route at {host}/{name} (app) and {host}/api/{name} (API). The whole
    site block — module routes, root redirect, catch-all — is generated into a
    single include (mod_site.caddy) that the base Caddyfile imports at top
    level. There are no hand-written per-module routes left: on first apply the
    legacy hand-tuned routes inside the old site block are parsed into
    ~/.mod/caddy/overrides.json (preserving special upstreams like the
    activator on :9000 or docker-name hosts) and the old block is replaced by
    the import.

    The host is a SETTING, not a constant. It defaults to modc2.com but the
    owner can point the router at any domain:

        m caddy/host mydomain.com

    …and this box becomes a mod router for that domain. Settings and overrides
    are deployment state, so they live off-tree in ~/.mod/caddy/ (never in the
    committed config.json).

    Convention (per module config.json):
      port      → API   (proxied at /api/{name}, prefix stripped)
      app_port  → app   (proxied at /{name}, prefix kept — Next basePath)
    """

    description = "The mod router — generates the whole {host}/{mod} Caddy site from module configs; host is owner-configurable so anyone can run a router."
    path = r'/root/mod/mod/orbit/caddy'

    CADDYFILE = os.environ.get("MOD_CADDYFILE", "/etc/caddy/Caddyfile")
    INCLUDE = "mod_site.caddy"     # relative to the Caddyfile's dir
    LEGACY_INCLUDE = "mod_routes.caddy"
    ADMIN = os.environ.get("MOD_CADDY_ADMIN", "localhost:2099")

    STATE_DIR = os.path.expanduser(os.environ.get("MOD_CADDY_STATE", "~/.mod/caddy"))

    DEFAULTS = {
        "host": "modc2.com",       # the router's public host — change with m caddy/host
        "upstream_host": "localhost",
        "root_redirect": "/web",   # where {host}/ lands; null disables
        "fallback": "localhost:3000",  # catch-all upstream; null disables
    }

    # Roots scanned for routable modules.
    ROOTS = ["/root/mod/mod/orbit", "/root/mod/mod/core"]

    # ── mod protocol ─────────────────────────────────────────────────────────
    def forward(self, **kwargs):
        return self.info()

    def info(self):
        s = self.settings()
        return {
            'name': 'caddy',
            'description': self.description,
            'path': self.path,
            'caddyfile': self.CADDYFILE,
            'host': s["host"],
            'state_dir': self.STATE_DIR,
            'fns': ['settings', 'host', 'discover', 'routes', 'generate',
                    'migrate', 'apply', 'reload'],
        }

    def readme(self):
        for name in ['README.md', 'readme.md']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    # ── settings (off-tree, owner-editable) ──────────────────────────────────
    def _settings_path(self):
        return os.path.join(self.STATE_DIR, "settings.json")

    def _overrides_path(self):
        return os.path.join(self.STATE_DIR, "overrides.json")

    def _read_json(self, path, default):
        try:
            return json.load(open(path))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path, data):
        os.makedirs(self.STATE_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def settings(self, **kwargs):
        """Get (no args) or set (key=value) router settings.

        Keys: host, upstream_host, root_redirect, fallback."""
        s = dict(self.DEFAULTS)
        s.update(self._read_json(self._settings_path(), {}))
        updates = {k: v for k, v in kwargs.items() if k in self.DEFAULTS}
        if updates:
            s.update(updates)
            self._write_json(self._settings_path(), s)
        return s

    def host(self, host=None, apply=True):
        """The router's public host. `m caddy/host mydomain.com` repoints the
        whole {host}/{mod} router at a new domain — that's how anyone turns a
        box into a router."""
        if host is None:
            return {"host": self.settings()["host"]}
        old = self.settings()["host"]
        self.settings(host=host)
        out = {"host": host, "was": old}
        if apply:
            out["apply"] = self.apply()
        return out

    def overrides(self):
        """Per-module hand-tuned routes (upstream + strip rules) that win over
        the config.json-derived defaults. Seeded by migrate()."""
        return self._read_json(self._overrides_path(), {})

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
        copy-pasted placeholder ports many dormant modules carry: without
        opt-in we'd hijack a port that's live but owned by a different
        service. `live_only` then drops any opted-in port that isn't actually
        listening, so we never add a dead upstream."""
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

    # ── legacy migration ─────────────────────────────────────────────────────
    def _find_site_block(self, text, host):
        """Return (start, end, inner) of the `host { ... }` block, or None."""
        mm = re.search(r'^%s\s*\{' % re.escape(host), text, re.M)
        if not mm:
            return None
        depth, i = 0, mm.end() - 1
        while i < len(text):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return (mm.start(), i + 1, text[mm.end():i])
            i += 1
        return None

    def migrate(self, save=True):
        """Parse the legacy hand-written routes out of the current site block
        into overrides (preserving activator :9000 upstreams, docker-name
        upstreams, and strip rules), plus root redirect / fallback settings."""
        s = self.settings()
        found = self._find_site_block(self._base_text(), s["host"])
        if not found:
            return {"error": f"no `{s['host']} {{` site block in {self.CADDYFILE} (already migrated?)",
                    "overrides": self.overrides()}
        inner = found[2]
        ov = self.overrides()
        for hit in re.finditer(
                r'@(\w+)_(api|app)\s+path[^\n]*\n\s*handle @\1_\2\s*\{(.*?)\n\s*\}',
                inner, re.S):
            name, kind, body = hit.group(1), hit.group(2), hit.group(3)
            up = re.search(r'reverse_proxy\s+(\S+)', body)
            if not up:
                continue
            upstream = up.group(1).replace("{$PM2_HOST:localhost}", s["upstream_host"])
            ov.setdefault(name, {})[kind] = {
                "upstream": upstream,
                "strip": "strip_prefix" in body,
            }
        redir = re.search(r'redir @root (\S+)', inner)
        fb = re.search(r'handle /\*\s*\{\s*reverse_proxy\s+(\S+)', inner)
        self.settings(
            root_redirect=redir.group(1) if redir else s["root_redirect"],
            fallback=fb.group(1).replace("{$PM2_HOST:localhost}", s["upstream_host"]) if fb else s["fallback"],
        )
        if save:
            self._write_json(self._overrides_path(), ov)
        return {"overrides": ov, "modules": sorted(ov.keys())}

    # ── route table ──────────────────────────────────────────────────────────
    def routes(self):
        """The merged route table: overrides (hand-tuned) win; every other
        opted-in module gets defaults from its config.json ports."""
        s = self.settings()
        table = {}
        for mod in self.discover():
            n = mod["name"]
            spec = {}
            if mod["api_port"]:
                spec["api"] = {"upstream": f'{s["upstream_host"]}:{mod["api_port"]}', "strip": True}
            if mod["app_port"]:
                spec["app"] = {"upstream": f'{s["upstream_host"]}:{mod["app_port"]}', "strip": False}
            if spec:
                table[n] = spec
        overridden = self.overrides()
        table.update(overridden)   # overrides win entirely per module
        return {"host": s["host"], "routes": table,
                "overridden": sorted(overridden.keys()),
                "auto": sorted(set(table) - set(overridden))}

    # ── generation ───────────────────────────────────────────────────────────
    def generate(self):
        """Build the COMPLETE site block for {host} — module routes, root
        redirect, catch-all — as the mod_site.caddy include."""
        s = self.settings()
        info = self.routes()
        lines = [
            "# ─────────────────────────────────────────────────────────────",
            "# AUTO-GENERATED by `m caddy/apply` — DO NOT EDIT BY HAND.",
            "# The whole {host}/{mod} router: every route derives from module",
            "# config.json ports plus ~/.mod/caddy/overrides.json.",
            f"# host={s['host']} generated_at_epoch={int(time.time())}",
            "# ─────────────────────────────────────────────────────────────",
            f"{s['host']} {{",
        ]
        for n in sorted(info["routes"]):
            spec = info["routes"][n]
            for kind, prefix in (("api", f"/api/{n}"), ("app", f"/{n}")):
                r = spec.get(kind)
                if not r:
                    continue
                lines.append(f"    @{n}_{kind} path {prefix} {prefix}/*")
                lines.append(f"    handle @{n}_{kind} {{")
                if r.get("strip"):
                    lines.append(f"        uri strip_prefix {prefix}")
                lines.append(f"        reverse_proxy {r['upstream']}")
                lines.append("    }")
        if s.get("root_redirect"):
            lines += [
                "    # Default landing. 302 (not 301) so it can be flipped later",
                "    # without fighting browser cache.",
                "    @root path /",
                f"    redir @root {s['root_redirect']} 302",
            ]
        if s.get("fallback"):
            lines += [
                "    handle /* {",
                f"        reverse_proxy {s['fallback']}",
                "    }",
            ]
        lines.append("}")
        return "\n".join(lines) + "\n"

    # ── apply ────────────────────────────────────────────────────────────────
    def apply(self, reload=True):
        """Write the include; on first run migrate the legacy hand-written site
        block into overrides and replace it with a top-level import. Validates
        before reloading and rolls back on failure."""
        s = self.settings()
        caddy_dir = os.path.dirname(self.CADDYFILE)
        include_path = os.path.join(caddy_dir, self.INCLUDE)
        result = {"include_path": include_path, "host": s["host"]}

        base = self._base_text()
        migrated = None
        if f"import {self.INCLUDE}" not in base:
            # One-time cutover: absorb the legacy block, then replace it.
            if not os.path.exists(self._overrides_path()):
                migrated = self.migrate()
                result["migrated"] = migrated.get("modules", [])
            found = self._find_site_block(base, s["host"])
            bak = f"{self.CADDYFILE}.bak.{int(time.time())}"
            with open(bak, "w") as f:
                f.write(base)
            result["backup"] = bak
            imp = f"import {self.INCLUDE}\n"
            if found:
                new_base = base[:found[0]] + imp + base[found[1]:].lstrip("\n")
            else:
                new_base = base.rstrip("\n") + "\n\n" + imp
            # the legacy per-module include is superseded — drop stale imports
            new_base = re.sub(r'^\s*import %s\n' % re.escape(self.LEGACY_INCLUDE),
                              "", new_base, flags=re.M)
            with open(self.CADDYFILE, "w") as f:
                f.write(new_base)
            result["base_rewritten"] = True

        with open(include_path, "w") as f:
            f.write(self.generate())

        v = self._validate()
        result["validate"] = v
        if not v["ok"]:
            if result.get("backup"):
                with open(self.CADDYFILE, "w") as f:
                    f.write(base)
                result["rolled_back"] = True
            return result

        routed = self.routes()
        result["routes"] = sorted(routed["routes"].keys())
        if reload:
            result["reload"] = self.reload()
        return result

    def _validate(self):
        try:
            r = subprocess.run(
                ["caddy", "validate", "--config", self.CADDYFILE, "--adapter", "caddyfile"],
                cwd=os.path.dirname(self.CADDYFILE),
                capture_output=True, text=True, timeout=60,
            )
            ok = r.returncode == 0
            return {"ok": ok, "stderr": "" if ok else r.stderr.strip()[-800:]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

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
