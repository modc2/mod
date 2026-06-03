import subprocess
import os
import json
import socket
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
MOD_ROOT = os.path.expanduser('~/mod/mod')
# Output Caddyfile at module root — Docker volume mount picks it up.
CADDYFILE = os.path.join(BASE, '..', 'Caddyfile')
PM2_NAME = 'caddy'


class Mod:
    """Dynamic Caddy reverse proxy manager for mod modules.

    Compiles per-module Caddyfile snippets into a single Caddyfile.
    Modules can place a ``Caddyfile`` in their root directory with
    route snippets.  Modules without one get auto-generated routes
    from their config.json ``urls`` block.

    Use ``{$PM2_HOST:localhost}`` in module Caddyfiles for the
    reverse-proxy host — Caddy resolves the env var at runtime.
    Set ``PM2_HOST=host.docker.internal`` in the Caddy Docker env
    so containerised Caddy can reach PM2 processes on the host.
    """

    def __init__(self, caddyfile=None):
        self.caddyfile = caddyfile or CADDYFILE

    # ── helpers ──────────────────────────────────────────────────

    def _port(self, url):
        try:
            return urlparse(url).port
        except Exception:
            return None

    def _live(self, port):
        if not port:
            return False
        try:
            with socket.create_connection(('localhost', port), timeout=0.3):
                return True
        except (ConnectionRefusedError, OSError, socket.timeout):
            return False

    def _docker_host(self, module_dir):
        """Extract container_name from docker-compose if present."""
        for fname in ('docker-compose.yaml', 'docker-compose.yml'):
            dc_path = os.path.join(module_dir, fname)
            if not os.path.isfile(dc_path):
                continue
            try:
                with open(dc_path) as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith('container_name:'):
                            return stripped.split(':', 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
        return None

    # ── scan ─────────────────────────────────────────────────────

    def _scan(self):
        """Scan orbit/ and core/ for modules with urls in config.json.

        Returns dict keyed by module name::

            {name: {api_port, app_port, host, caddyfile: str|None}}

        If a module has its own ``Caddyfile``, ``caddyfile`` contains
        its raw content and auto-generation is skipped for that module.
        """
        modules = {}
        for d in ['orbit', 'core']:
            search = os.path.join(MOD_ROOT, d)
            if not os.path.isdir(search):
                continue
            for name in sorted(os.listdir(search)):
                mod_dir = os.path.join(search, name)
                cfg_path = os.path.join(mod_dir, 'config.json')
                if not os.path.isfile(cfg_path):
                    continue
                try:
                    with open(cfg_path) as f:
                        cfg = json.load(f)
                except Exception:
                    continue
                if 'data' in cfg and isinstance(cfg['data'], dict):
                    cfg = cfg['data']
                urls = cfg.get('urls') or cfg.get('url') or {}
                app_url, api_url = urls.get('app'), urls.get('api')
                if not app_url and not api_url:
                    continue

                # Check for a local Caddyfile snippet
                local_cf = os.path.join(mod_dir, 'Caddyfile')
                caddyfile_content = None
                if os.path.isfile(local_cf):
                    try:
                        with open(local_cf) as f:
                            caddyfile_content = f.read().rstrip()
                    except Exception:
                        pass

                # Resolve host for auto-generated routes
                mode = cfg.get('mode')
                if mode == 'pm2':
                    host = '{$PM2_HOST:localhost}'
                elif mode == 'docker':
                    host = self._docker_host(mod_dir) or '{$PM2_HOST:localhost}'
                else:
                    docker_host = self._docker_host(mod_dir)
                    host = docker_host or '{$PM2_HOST:localhost}'

                modules[name] = {
                    'app_port': self._port(app_url),
                    'api_port': self._port(api_url),
                    'host': host,
                    'caddyfile': caddyfile_content,
                }
        return modules

    def _filter_live(self, modules):
        """Remove modules with no live ports, null out dead individual ports.

        Docker-hosted modules and modules with local Caddyfiles skip
        liveness checks.
        """
        dead = []
        for name, info in modules.items():
            host = info.get('host', 'localhost')
            # Skip liveness for docker containers and env-var hosts
            if host != 'localhost' and host != '{$PM2_HOST:localhost}':
                continue
            # Skip liveness for modules with their own Caddyfile
            if info.get('caddyfile'):
                continue
            app_live = self._live(info.get('app_port'))
            api_live = self._live(info.get('api_port'))
            if not app_live and not api_live:
                dead.append(name)
            else:
                if not app_live:
                    info['app_port'] = None
                if not api_live:
                    info['api_port'] = None
        for name in dead:
            del modules[name]
        if dead:
            print(f'Skipped (not live): {", ".join(sorted(dead))}')
        return modules

    # ── route generation ─────────────────────────────────────────

    @staticmethod
    def _split_caddyfile(content):
        """Split a module Caddyfile into route snippets and domain blocks.

        Route snippets (``@matcher``, ``handle``, etc.) go inside the
        main domain block.  Domain blocks (``sub.domain.com { … }``)
        become separate top-level entries.  Block comments ``/* … */``
        are dropped.
        """
        routes, blocks = [], []
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            # skip empties
            if not stripped:
                i += 1
                continue
            # skip block comments  /* ... */
            if stripped.startswith('/*'):
                while i < len(lines) and '*/' not in lines[i]:
                    i += 1
                i += 1  # skip closing */ line
                continue
            # domain block: non-indented line with { that isn't a route directive
            if (not lines[i][0].isspace()
                    and '{' in stripped
                    and not stripped.startswith(('@', 'handle', 'header',
                        'uri', 'respond', 'reverse_proxy', 'redir',
                        'encode', 'log', 'tls', 'import'))):
                # Skip bare global options blocks (`{ ... }` with no key).
                # Caddy only allows one global block (must be first); the
                # main Caddyfile already defines it, so module-local ones
                # would crash the reload.
                is_global = stripped.split('{', 1)[0].strip() == ''
                block = []
                depth = 0
                while i < len(lines):
                    block.append(lines[i])
                    depth += lines[i].count('{') - lines[i].count('}')
                    i += 1
                    if depth <= 0:
                        break
                if not is_global:
                    blocks.append('\n'.join(block))
            else:
                routes.append(lines[i])
                i += 1
        return '\n'.join(routes).strip(), '\n\n'.join(blocks).strip()

    def _module_routes(self, modules):
        """Build route blocks for all modules.

        Modules with a local Caddyfile: route snippets are included
        inside the main domain; domain blocks are collected separately.
        Modules without one get auto-generated routes from config.json.

        Returns ``(route_lines, extra_blocks)`` where extra_blocks is
        a list of top-level domain block strings.
        """
        lines = []
        extra = []
        for name in sorted(modules):
            info = modules[name]
            cf = info.get('caddyfile')
            if cf:
                routes, blocks = self._split_caddyfile(cf)
                if routes:
                    for raw_line in routes.split('\n'):
                        lines.append(f'    {raw_line}')
                if blocks:
                    extra.append(blocks)
            else:
                host = info.get('host', '{$PM2_HOST:localhost}')
                api_port = info.get('api_port')
                app_port = info.get('app_port')
                if api_port:
                    lines.append(f'    @{name}_api path /api/{name} /api/{name}/*')
                    lines.append(f'    handle @{name}_api {{')
                    lines.append(f'        uri strip_prefix /api/{name}')
                    lines.append(f'        reverse_proxy {host}:{api_port}')
                    lines.append(f'    }}')
                if app_port:
                    lines.append(f'    @{name}_app path /{name} /{name}/*')
                    lines.append(f'    handle @{name}_app {{')
                    lines.append(f'        reverse_proxy {host}:{app_port}')
                    lines.append(f'    }}')
        return lines, extra

    # ── generate / sync ─────────────────────────────────────────

    def generate(self, domain='modc2.com', app_port=3000, api_port=8000,
                 admin_port=2099, check_ports=True, **kwargs):
        """Compile per-module Caddyfile snippets into a single Caddyfile.

        For each module in orbit/ and core/:
          - If it has a ``Caddyfile``, include it verbatim.
          - Otherwise auto-generate routes from config.json urls.

        Extra domain blocks (e.g. api.modc2.com) from the existing
        Caddyfile are preserved.
        """
        modules = self._scan()
        if check_ports:
            modules = self._filter_live(modules)

        route_lines, module_blocks = self._module_routes(modules)

        lines = [
            '{',
            f'    admin localhost:{admin_port}',
            '}',
            '',
            f'{domain} {{',
            *route_lines,
            f'    @main_api path /api /api/*',
            f'    handle @main_api {{',
            f'        uri strip_prefix /api',
            f'        reverse_proxy {{$PM2_HOST:localhost}}:{api_port}',
            f'    }}',
            f'    handle /* {{',
            f'        reverse_proxy {{$PM2_HOST:localhost}}:{app_port}',
            f'    }}',
            '}',
            '',
        ]

        # Append domain blocks from module Caddyfiles (e.g. api.modc2.com)
        for block in module_blocks:
            lines.append(block)
            lines.append('')

        content = '\n'.join(lines)
        print(content)
        return content

    # ── file / process management ────────────────────────────────

    def _write(self, content):
        with open(self.caddyfile, 'w') as f:
            f.write(content)

    def _run(self, cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, **kw)

    def _pm2_start(self, cmd):
        self._run(['pm2', 'delete', PM2_NAME])
        result = self._run(['pm2', 'start', cmd[0], '--name', PM2_NAME, '--'] + cmd[1:])
        return result.returncode == 0

    def _stop_stale(self):
        try:
            self._run(['caddy', 'stop'], timeout=3)
        except subprocess.TimeoutExpired:
            self._run(['pkill', '-f', 'caddy run'])
            self._run(['pkill', 'caddy'])

    def _caddy_in_docker(self):
        """Check if Caddy is running as a Docker container."""
        try:
            result = self._run(
                ['docker', 'inspect', '-f', '{{.State.Running}}', 'caddy'],
                timeout=3,
            )
            return result.stdout.strip() == 'true'
        except Exception:
            return False

    def _reload(self):
        """Reload Caddy config — via docker exec if in Docker, else locally."""
        if self._caddy_in_docker():
            result = self._run(
                ['docker', 'exec', 'caddy', 'caddy', 'reload',
                 '--config', '/etc/caddy/Caddyfile'],
                timeout=5,
            )
        else:
            result = self._run(
                ['caddy', 'reload', '--config', self.caddyfile],
                timeout=5,
            )
        return result.returncode == 0

    def sync(self, **kwargs):
        """Generate Caddyfile, write it, reload if running."""
        self._write(self.generate(**kwargs))
        try:
            reloaded = self._reload()
        except subprocess.TimeoutExpired:
            reloaded = False
        return {'status': 'ok', 'caddyfile': self.caddyfile, 'reloaded': reloaded}

    def serve(self, **kwargs):
        """Generate Caddyfile and start Caddy via PM2."""
        self._write(self.generate(**kwargs))
        self._stop_stale()
        ok = self._pm2_start(['caddy', 'run', '--config', self.caddyfile])
        return {'status': 'ok' if ok else 'error', 'pm2': PM2_NAME, 'caddyfile': self.caddyfile}

    def kill(self):
        """Stop Caddy PM2 process and any stale daemon."""
        result = self._run(['pm2', 'delete', PM2_NAME])
        self._stop_stale()
        return {'status': 'killed' if result.returncode == 0 else 'error', 'pm2': PM2_NAME}

    def restart(self, **kwargs):
        """Kill and re-serve."""
        self.kill()
        return self.serve(**kwargs)

    def status(self):
        """Check if Caddy is running via PM2."""
        try:
            result = self._run(['pm2', 'jlist'], timeout=10)
            for p in json.loads(result.stdout or '[]'):
                if p.get('name') == PM2_NAME:
                    env = p.get('pm2_env', {})
                    return {
                        'running': env.get('status') == 'online',
                        'status': env.get('status'),
                        'pid': p.get('pid'),
                    }
        except Exception:
            pass
        return {'running': False}

    def logs(self, lines=50):
        """Show recent PM2 logs."""
        result = self._run(['pm2', 'logs', PM2_NAME, '--nostream', '--lines', str(lines)])
        return result.stdout + result.stderr
