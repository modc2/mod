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
    gets a route at {host}/{name} (app) and {host}/{name}/api (API; the legacy
    {host}/api/{name} form is kept as an alias for old clients). The whole
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
      port      → API   (proxied at /{name}/api, prefix stripped;
                         legacy /api/{name} kept as an alias)
      app_port  → app   (proxied at /{name}, prefix kept — Next basePath;
                         app-internal Next API routes live at /{name}/_api)
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
        "hosts": {},               # extra domains → {root_redirect?, fallback?}; add with m caddy/add_host
    }

    # Roots scanned for routable modules.
    ROOTS = ["/root/mod/mod/orbit", "/root/mod/mod/core"]

    # A module the build console has marked PRIVATE is not published here.
    # Privacy is an owner's opt-out of being visible at all — dropping it from
    # the hub while {host}/{mod} still answers would be a hole, so the router
    # reads the same per-module records build writes (0600, off-tree) and
    # simply never generates a route for one. Going public again restores the
    # route on the next apply.
    PRIVATE_DIR = os.path.expanduser(
        os.environ.get("MOD_PRIVATE_STATE", "~/.mod/build/private")
    )

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
            'fns': ['settings', 'host', 'add_host', 'remove_host', 'discover',
                    'routes', 'generate', 'migrate', 'apply', 'reload',
                    'alias', 'unalias', 'aliases'],
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

    def add_host(self, host, root_redirect=None, fallback=None, apply=True):
        """Serve the same generated router on an ADDITIONAL domain, with its
        own default landing. Routes are identical to the primary host; only
        where `/` lands (and optionally the catch-all) differs.

            m caddy/add_host modc2c.com root_redirect=/claude

        …makes modc2c.com a second front door whose null page is claude."""
        s = self.settings()
        hosts = dict(s.get("hosts") or {})
        spec = {}
        if root_redirect is not None:
            spec["root_redirect"] = root_redirect
        if fallback is not None:
            spec["fallback"] = fallback
        hosts[host] = spec
        self.settings(hosts=hosts)
        out = {"host": host, "hosts": hosts}
        if apply:
            out["apply"] = self.apply()
        return out

    def remove_host(self, host, apply=True):
        """Drop an extra domain added with add_host."""
        s = self.settings()
        hosts = dict(s.get("hosts") or {})
        removed = hosts.pop(host, None)
        self.settings(hosts=hosts)
        out = {"host": host, "removed": removed is not None, "hosts": hosts}
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
                if self._is_private(cfg.get("name", name)):
                    continue
                api = cfg.get("port") or (cfg.get("ports") or {}).get("api")
                app = cfg.get("app_port") or (cfg.get("ports") or {}).get("app")
                if not api and not app:
                    continue
                # `api_replicas`: extra ports running the SAME api over shared
                # state. They join the primary in one load-balanced upstream
                # pool; Caddy's own health checks (not this regen-time
                # liveness snapshot) decide which members get traffic, so a
                # replica restarting at regen time stays in the pool.
                replicas = []
                for p in [api] + list(cfg.get("api_replicas") or []):
                    try:
                        p = int(p)
                    except (TypeError, ValueError):
                        continue
                    if p and p not in replicas:
                        replicas.append(p)
                # `app_replicas`: the same for the app (one per build node).
                app_pool = []
                for p in [app] + list(cfg.get("app_replicas") or []):
                    try:
                        p = int(p)
                    except (TypeError, ValueError):
                        continue
                    if p and p not in app_pool:
                        app_pool.append(p)
                if live_only:
                    # Keep only the ports that are actually serving. A replica
                    # pool counts as live while ANY member serves.
                    pool_live = len(replicas) > 1 and any(self._port_live(p) for p in replicas)
                    api = api if (pool_live or self._port_live(api)) else None
                    app_live = len(app_pool) > 1 and any(self._port_live(p) for p in app_pool)
                    app = app if (app_live or self._port_live(app)) else None
                    if not api and not app:
                        continue
                out.append({
                    "name": cfg.get("name", name),
                    "dir": d,
                    "api_port": api,
                    "api_replicas": replicas if api and len(replicas) > 1 else [],
                    "app_port": app,
                    "app_replicas": app_pool if app and len(app_pool) > 1 else [],
                    "aliases": [a for a in (cfg.get("aliases") or []) if isinstance(a, str)],
                })
        return out

    def _is_private(self, name):
        """Has this module been made private in the build console?

        The record is `~/.mod/build/private/<name>.json` with `enabled: true`
        (nested names flatten `/` to `__`, same as build writes them). Any
        read failure means "not private" — the router must never fail closed
        on a missing state dir and take the whole fleet off the air."""
        safe = "".join(c for c in name if c.isalnum() or c in "-_/").replace("/", "__")
        if not safe:
            return False
        try:
            with open(os.path.join(self.PRIVATE_DIR, f"{safe}.json")) as f:
                return bool(json.load(f).get("enabled"))
        except (OSError, json.JSONDecodeError, AttributeError):
            return False

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
        discovered = self.discover()
        for mod in discovered:
            n = mod["name"]
            spec = {}
            if mod["api_port"]:
                spec["api"] = {"upstream": f'{s["upstream_host"]}:{mod["api_port"]}', "strip": True}
                if mod.get("api_replicas"):
                    spec["api"]["upstreams"] = [f'{s["upstream_host"]}:{p}' for p in mod["api_replicas"]]
            if mod["app_port"]:
                spec["app"] = {"upstream": f'{s["upstream_host"]}:{mod["app_port"]}', "strip": False}
                if mod.get("app_replicas"):
                    spec["app"]["upstreams"] = [f'{s["upstream_host"]}:{p}' for p in mod["app_replicas"]]
            if spec:
                table[n] = spec
        overridden = self.overrides()
        table.update(overridden)   # overrides win entirely per module
        alias_info = self._resolve_aliases(table, discovered)
        return {"host": s["host"], "routes": table,
                "overridden": sorted(overridden.keys()),
                "auto": sorted(set(table) - set(overridden)),
                "aliases": alias_info["aliases"],
                "alias_conflicts": alias_info["conflicts"]}

    # ── aliases ──────────────────────────────────────────────────────────────
    # An alias is a second public name for a routed module: {host}/{alias}/...
    # 308-redirects to {host}/{target}/... (path, query, method and body kept).
    # A redirect, not a proxy, because Next apps bake their basePath into every
    # asset URL — proxying /hub to an app built for /build would load a page
    # whose CSS/JS all point back at /build anyway.
    #
    # Two sources:
    #   config.json "aliases": ["studio", ...]  — ships with the module; only
    #       takes a name nobody else uses (never a module dir or a route).
    #   overrides.json "<alias>": {"alias": "<target>"}  — a deliberate
    #       takeover of a name that IS in use; written by alias(force=True).
    ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
    RESERVED = {"api", "_next", "_api", "static", "public", "favicon.ico"}

    def _module_names(self):
        names = set()
        for root in self.ROOTS:
            if os.path.isdir(root):
                names.update(n.lower() for n in os.listdir(root)
                             if os.path.isdir(os.path.join(root, n)))
        return names

    def _resolve_aliases(self, table, discovered):
        """Fold config.json aliases into the route table (in place). Returns
        {aliases: {alias: target}, conflicts: {alias: reason}}."""
        aliases, conflicts = {}, {}
        taken = self._module_names()
        for alias, spec in table.items():
            if isinstance(spec, dict) and spec.get("alias"):
                aliases[alias] = spec["alias"]
        for mod in discovered:
            target = mod["name"]
            for alias in mod.get("aliases", []):
                alias = alias.lower()
                if alias in aliases and aliases[alias] == target:
                    continue
                reason = None
                if not self.ALIAS_RE.match(alias) or alias in self.RESERVED:
                    reason = "invalid name"
                elif alias in table or alias in taken:
                    reason = f"name in use — `m caddy/alias {alias} {target} force=True` takes it over"
                elif target not in table:
                    reason = f"{target} is not routed"
                if reason:
                    conflicts[alias] = reason
                    continue
                table[alias] = {"alias": target}
                aliases[alias] = target
        # an alias must land on a real route, never on another alias
        for alias, target in list(aliases.items()):
            t = table.get(target)
            if not isinstance(t, dict) or "alias" in t or not (t.get("app") or t.get("api")):
                conflicts[alias] = f"{target} is not routed"
                table.pop(alias, None)
                aliases.pop(alias)
        return {"aliases": dict(sorted(aliases.items())), "conflicts": conflicts}

    def aliases(self):
        """Every live alias → target, plus any declared alias that was refused."""
        r = self.routes()
        return {"aliases": r["aliases"], "conflicts": r["alias_conflicts"]}

    def _config_for(self, name):
        for mod in self.discover(live_only=False):
            if mod["name"] == name:
                return os.path.join(mod["dir"], "config.json")
        return None

    def alias(self, name, to, force=False, apply=True):
        """Give module `to` a second public name.

            m caddy/alias studio build              # {host}/studio → {host}/build
            m caddy/alias hub build force=True      # take over a name in use

        Without force the alias is written to the target's config.json
        ("aliases": [...]) and only claims a free name. With force it goes in
        overrides.json and REPLACES whatever {host}/{name} served before —
        that module's own route is gone until you `m caddy/unalias {name}`."""
        name = str(name).lower().strip("/")
        if not self.ALIAS_RE.match(name) or name in self.RESERVED:
            return {"error": f"invalid alias name: {name!r}"}
        if name == to:
            return {"error": "an alias can't point at itself"}
        table = self.routes()["routes"]
        target = table.get(to)
        if not isinstance(target, dict) or "alias" in target or not (target.get("app") or target.get("api")):
            return {"error": f"{to} is not a routed module (needs route:true + a live port, or an override)"}
        in_use = (name in table and table[name].get("alias") != to) or name in self._module_names()
        if force:
            ov = self.overrides()
            prev = ov.get(name)
            ov[name] = {"alias": to}
            if prev and "alias" not in prev:
                # keep the displaced route so unalias can put it back
                ov[name]["displaced"] = prev
            self._write_json(self._overrides_path(), ov)
            where = "overrides.json"
        else:
            if in_use:
                return {"error": f"/{name} is already in use — pass force=True to take it over",
                        "current": table.get(name)}
            cfg_path = self._config_for(to)
            if not cfg_path:
                return {"error": f"{to} has no config.json under {self.ROOTS} — use force=True to alias via overrides"}
            cfg = json.load(open(cfg_path))
            al = [a for a in (cfg.get("aliases") or []) if a != name] + [name]
            cfg["aliases"] = al
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
                f.write("\n")
            where = cfg_path
        out = {"alias": name, "to": to, "stored_in": where, "took_over": bool(force and in_use)}
        if apply:
            out["apply"] = self.apply()
        return out

    def unalias(self, name, apply=True):
        """Remove an alias wherever it was declared. A forced takeover restores
        the route it displaced."""
        name = str(name).lower().strip("/")
        removed = []
        ov = self.overrides()
        spec = ov.get(name)
        if isinstance(spec, dict) and "alias" in spec:
            if spec.get("displaced"):
                ov[name] = spec["displaced"]
            else:
                del ov[name]
            self._write_json(self._overrides_path(), ov)
            removed.append("overrides.json")
        for mod in self.discover(live_only=False):
            if name in [a.lower() for a in mod.get("aliases", [])]:
                p = os.path.join(mod["dir"], "config.json")
                cfg = json.load(open(p))
                cfg["aliases"] = [a for a in cfg.get("aliases", []) if a.lower() != name]
                if not cfg["aliases"]:
                    del cfg["aliases"]
                with open(p, "w") as f:
                    json.dump(cfg, f, indent=4)
                    f.write("\n")
                removed.append(p)
        out = {"alias": name, "removed_from": removed}
        if apply and removed:
            out["apply"] = self.apply()
        return out

    # ── generation ───────────────────────────────────────────────────────────
    # How a replica pool is balanced. `cookie` pins each browser to one
    # replica (its live job streams stay put) while new browsers spread over
    # the pool; a dead or draining member (/health 503) drops out within one
    # health interval, and a request whose dial fails is retried on another.
    LB_COOKIE = "mod_lb"

    def _proxy_lines(self, spec, name="", indent="        ", active_health=True):
        ups = spec.get("upstreams") or []
        if len(ups) < 2:
            return [f"{indent}reverse_proxy {spec['upstream']}"]
        i2 = indent + "    "
        # App pools (`app_replicas`) have no /health of their own — they are
        # checked passively: a failed dial marks the member down for
        # fail_duration and the request is retried on another one.
        health = [
            f"{i2}health_uri /health",
            f"{i2}health_interval 3s",
            f"{i2}health_timeout 2s",
        ] if active_health else []
        return [
            f"{indent}reverse_proxy {' '.join(ups)} {{",
            # one cookie per module: pools never overwrite each other's pin
            f"{i2}lb_policy cookie {self.LB_COOKIE}_{re.sub(r'[^A-Za-z0-9_]', '_', name)}",
            f"{i2}lb_try_duration 5s",
            f"{i2}lb_try_interval 250ms",
            f"{i2}fail_duration 20s",
            *health,
            f"{i2}flush_interval -1",
            f"{indent}}}",
        ]

    def _site_lines(self, host, routes, root_redirect, fallback):
        """One `host { ... }` site block: module routes + root landing +
        catch-all. Matcher names are per-site in Caddy, so the same names can
        repeat across host blocks."""
        lines = [f"{host} {{"]
        # JSON access log — the traffic module tails it for live users per app.
        log = self.settings().get("access_log", "/var/log/caddy/access.log")
        if log:
            lines += [
                "    log {",
                f"        output file {log} {{",
                "            roll_size 50MiB",
                "            roll_keep 5",
                "        }",
                "        format json",
                "    }",
            ]
        for n in sorted(routes):
            spec = routes[n]
            if spec.get("alias"):
                # {host}/{n}/rest?q → 308 {host}/{target}/rest?q — redirect
                # (not proxy) so basePath-baked apps load their own assets.
                lines.append(f"    @{n}_alias path /{n} /{n}/*")
                # The inner `route` pins strip-then-redir order (Caddy sorts
                # redir before uri inside a plain handle).
                lines.append(f"    handle @{n}_alias {{")
                lines.append("        route {")
                lines.append(f"            uri strip_prefix /{n}")
                lines.append(f"            redir * /{spec['alias']}{{uri}} 308")
                lines.append("        }")
                lines.append("    }")
                continue
            api = spec.get("api")
            if api:
                # Canonical API route is {host}/{n}/api — emitted BEFORE the
                # app handle so it wins the /{n}/* overlap. The legacy
                # /api/{n} form stays as an alias so old clients keep working.
                for tag, prefix in ((f"{n}_api", f"/{n}/api"),
                                    (f"{n}_api_legacy", f"/api/{n}")):
                    lines.append(f"    @{tag} path {prefix} {prefix}/*")
                    lines.append(f"    handle @{tag} {{")
                    if api.get("strip"):
                        lines.append(f"        uri strip_prefix {prefix}")
                    elif prefix == f"/{n}/api":
                        # Non-strip upstreams (the activator) speak the legacy
                        # /api/{n} shape — normalize before proxying.
                        lines.append(f"        uri replace /{n}/api /api/{n}")
                    lines += self._proxy_lines(api, n)
                    lines.append("    }")
            r = spec.get("app")
            if r:
                prefix = f"/{n}"
                lines.append(f"    @{n}_app path {prefix} {prefix}/*")
                lines.append(f"    handle @{n}_app {{")
                if r.get("strip"):
                    lines.append(f"        uri strip_prefix {prefix}")
                lines += self._proxy_lines(r, n, active_health=False)
                lines.append("    }")
        # A page load that hits a dead upstream (module crashed, booting,
        # mid-restart) gets the activator's "starting" placeholder instead
        # of a bare 502; the placeholder also wakes the module and reloads
        # itself once it answers. API calls and fetches keep the real error.
        wait = self.settings().get("placeholder_upstream", "localhost:9000")
        if wait:
            lines += [
                "    handle_errors {",
                "        @mod_starting expression `{http.error.status_code} in [502, 503, 504] && {http.request.method} == 'GET' && ({http.request.header.Sec-Fetch-Mode} == 'navigate' || ({http.request.header.Sec-Fetch-Mode} == '' && {http.request.header.Accept}.contains('text/html')))`",
                "        handle @mod_starting {",
                "            rewrite * /_placeholder",
                f"            reverse_proxy {wait} {{",
                "                header_up X-Mod-Orig {http.request.orig_uri.path}",
                "            }",
                "        }",
                "    }",
            ]
        if root_redirect:
            lines += [
                "    # Default landing. 302 (not 301) so it can be flipped later",
                "    # without fighting browser cache.",
                "    @root path /",
                f"    redir @root {root_redirect} 302",
            ]
        if fallback:
            lines += [
                "    handle /* {",
                f"        reverse_proxy {fallback}",
                "    }",
            ]
        lines.append("}")
        return lines

    def generate(self):
        """Build the COMPLETE site block(s) — primary host plus any extra
        hosts, sharing one route table — as the mod_site.caddy include."""
        s = self.settings()
        info = self.routes()
        extra = s.get("hosts") or {}
        lines = [
            "# ─────────────────────────────────────────────────────────────",
            "# AUTO-GENERATED by `m caddy/apply` — DO NOT EDIT BY HAND.",
            "# The whole {host}/{mod} router: every route derives from module",
            "# config.json ports plus ~/.mod/caddy/overrides.json.",
            f"# host={s['host']} extra_hosts={','.join(sorted(extra)) or 'none'} generated_at_epoch={int(time.time())}",
            "# ─────────────────────────────────────────────────────────────",
        ]
        lines += self._site_lines(s["host"], info["routes"],
                                  s.get("root_redirect"), s.get("fallback"))
        for host in sorted(extra):
            spec = extra[host] or {}
            lines.append("")
            lines += self._site_lines(
                host, info["routes"],
                spec.get("root_redirect", s.get("root_redirect")),
                spec.get("fallback", s.get("fallback")),
            )
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
