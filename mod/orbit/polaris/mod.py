"""polaris — the Polaris GPU cloud as a mod protocol module.

https://polaris.computer sells GPUs and CPUs by the second, one-click template
deployments on top of them, and fronts a 400-model inference catalog. This
module puts all of it behind mod's verbs, so the same question has the same
answer from a shell, a browser and an agent:

    m polaris/gpus max_usd_hr=2                 # the catalog, cheapest first
    m polaris/quote gpu_type='H100 SXM5 80GB' hours=4
    m polaris/rent gpu_type='H100 SXM5 80GB' confirm=1   # spends YOUR credits
    m polaris/instances                         # what you are paying for now
    m polaris/ssh                               # how to get into it
    m polaris/stop instance_id=…                # what ends the billing

The same surface is REST (`m polaris/serve`, then GET /gpus), a browser console
at /polaris, and MCP tools (`polaris_gpus`, `polaris_rent`, …) at POST /mcp —
one implementation under all four, so they cannot drift.

The key is the caller's own: POLARIS_KEY, or ~/.mod/polaris/api_key at 0600.
Never config.json, never the repo — a key in the tree is a key in git history.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything importing it after us.
if HERE not in sys.path:
    sys.path.append(HERE)

from client import CONFIRM_USD, KEY_FILE, Polaris, PolarisError, ssh_key  # noqa: E402


class Mod:
    description = """
    polaris — the Polaris GPU cloud (polaris.computer) behind mod's verbs.
    Catalog and pricing are public; renting, deployments, billing and the
    account are the caller's own key. GPUs and CPUs by the second, sixteen
    one-click templates, and a 400-model inference catalog. REST + browser
    console + MCP on one port. Stopping an instance is what ends the billing.
    """

    def __init__(self, key=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50870))
        self.base = cfg.get('base_path', '/polaris')
        self._key = key

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except OSError:
            return {}

    @property
    def api(self):
        """A client bound to whichever key this caller resolved to."""
        return Polaris(key=self._key)

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    # ── catalog (public — no key needed) ─────────────────────────

    def gpus(self, gpu=None, min_vram_gb=None, max_usd_hr=None, kind=None,
             available_only=True, sort='price', limit=None):
        """The rentable catalog: GPUs and CPU boxes, spot and on-demand, cheapest
        first. Filter by name, VRAM, price or kind ('gpu'/'cpu')."""
        return self.api.gpus(gpu=gpu, min_vram_gb=min_vram_gb, max_usd_hr=max_usd_hr,
                             kind=kind, available_only=available_only,
                             sort=sort, limit=limit)

    ls = gpus

    def pricing(self):
        """The billing table — the rates your usage is actually charged at."""
        return self.api.pricing()

    def templates(self, category=None, q=None):
        """The one-click images: Ollama, Jupyter, ComfyUI, agent runtimes,
        raw docker. Deploy one with `m polaris/deploy template=ollama`."""
        return self.api.templates(category=category, q=q)

    def template(self, template_id):
        """One template in full, with the parameters a deploy accepts."""
        return self.api.template(template_id)

    def models(self, q=None, provider=None, max_prompt_price=None,
               min_context=None, sort='name', limit=60):
        """Polaris's inference catalog — 400+ models with per-token pricing.
        Filtering happens here; the upstream returns the whole list."""
        return self.api.models(q=q, provider=provider, max_prompt_price=max_prompt_price,
                               min_context=min_context, sort=sort, limit=limit)

    # ── renting ──────────────────────────────────────────────────

    def quote(self, gpu_type, hours=1, spot=True, quantity=1):
        """What a rental costs before you commit, plus anything cheaper with
        the same VRAM. Costs nothing to ask."""
        return self.api.quote(gpu_type, hours=hours, spot=spot, quantity=quantity)

    def rent(self, gpu_type, name='mod', hours=1, confirm=False, ssh_public_key=None,
             spot=True, quantity=1, **extra):
        """Provision an instance. Spends YOUR credits — anything estimated above
        $%s needs confirm=1. Billing runs until `stop`, not until `hours`.""" % CONFIRM_USD
        return self.api.rent(gpu_type, name=name, hours=hours, confirm=confirm,
                             ssh_public_key=ssh_public_key, spot=spot,
                             quantity=quantity, **extra)

    up = rent

    def instances(self, state=None):
        """Every instance on your account, with the combined burn rate."""
        return self.api.instances(state=state)

    ps = instances

    def instance(self, instance_id):
        """One instance, by id or by name."""
        return self.api.instance(instance_id)

    def ssh(self, instance_id=None):
        """The ssh command for a running box, once it has an address."""
        return self.api.ssh(instance_id)

    def stop(self, instance_id):
        """Terminate an instance. This is what ends the billing."""
        return self.api.stop(instance_id)

    down = stop

    # ── deployments ──────────────────────────────────────────────

    def deployments(self, state=None, kind=None):
        """Template deployments — a workload running on a box, with endpoints."""
        return self.api.deployments(state=state, kind=kind)

    def deployment(self, deployment_id):
        """One deployment in full: host, port, progress, parameters."""
        return self.api.deployment(deployment_id)

    def logs(self, deployment_id, tail=200):
        """Provisioning and runtime logs for a deployment."""
        return self.api.deployment_logs(deployment_id, tail=tail)

    def activity(self, limit=25):
        """The account's event tape: deployments, agent output, failures."""
        return self.api.activity(limit=limit)

    # ── account and money ────────────────────────────────────────

    def account(self):
        """Who the key belongs to, and the quota attached to it."""
        return self.api.account()

    def credits(self):
        """The prepaid balance. `status` is what gates new instances."""
        return self.api.credits()

    balance = credits

    def history(self, limit=20, offset=0):
        """The credit ledger — every debit, with what caused it."""
        return self.api.history(limit=limit, offset=offset)

    def packs(self):
        """Top-up sizes. Buying is a browser flow at polaris.computer."""
        return self.api.packs()

    def usage(self, billing=True):
        """Two meters: API requests, and compute seconds against the bill."""
        return self.api.usage(billing=billing)

    def stats(self):
        """Deployment and spend counters as the upstream dashboard shows them."""
        return self.api.stats()

    def keys(self):
        """Your Polaris API keys — metadata only, never the secrets."""
        return self.api.api_keys()

    def status(self):
        """Money, boxes and deployments in one call, plus hours of runway."""
        return self.api.status()

    # ── credentials ──────────────────────────────────────────────

    def set_key(self, key, persist=True):
        """Store a Polaris key at %s, 0600 and off-tree.""" % KEY_FILE
        return Polaris.set_key(key, persist=persist)

    def identity(self):
        """The SSH public key this box would hand to Polaris at rent time."""
        import auth
        pub = ssh_key()
        return {'ssh_public_key': pub,
                'source': '~/.ssh/*.pub' if pub else None,
                'polaris_key': 'set' if self.api.has_key() else 'missing',
                'key_file': KEY_FILE, **auth.state()}

    def token(self):
        """This module's owner token — paste it into a published console."""
        import auth
        return {'token': auth.secret(), 'file': auth.SECRET_FILE,
                'use': 'Authorization: Bearer <token>'}

    def raw(self, path, method='GET', body=None, params=None, auth=True):
        """Polaris's own API, unnormalized — for routes not wrapped here.
        `path` is relative to https://api.polaris.computer/api."""
        return self.api.raw(path, method=method, body=body, params=params, auth=auth)

    # ── serving ──────────────────────────────────────────────────

    def serve(self, port=None, background=True):
        """Run the REST API, the browser console and the MCP server on one port."""
        port = int(port or self.port)
        script = os.path.join(HERE, '_serve.sh')
        with open(script, 'w') as f:
            f.write(f'#!/bin/bash\ncd {HERE}\nexec python3 api.py --port {port}\n')
        os.chmod(script, 0o755)
        if not background:
            subprocess.call(['bash', script])
            return {'status': 'exited', 'port': port}
        try:
            import mod as m
            pm2 = m.mod('pm.pm2')()
            if pm2.exists('polaris'):
                pm2.kill('polaris', remove_script=False)
            pm2.start_script(name='polaris', script_path=script, cwd=HERE,
                             interpreter='bash')
            manager = 'pm2'
            pid = None
        except Exception:
            proc = subprocess.Popen(['bash', script], cwd=HERE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            manager, pid = 'subprocess', proc.pid
        return {'status': 'running', 'port': port, 'manager': manager, 'pid': pid,
                'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp'}

    def kill(self):
        """Stop the server. Does not touch anything you have rented."""
        try:
            import mod as m
            pm2 = m.mod('pm.pm2')()
            if pm2.exists('polaris'):
                pm2.kill('polaris')
                return {'killed': 'polaris', 'manager': 'pm2'}
        except Exception:
            pass
        out = subprocess.run(['pkill', '-f', f'api.py --port {self.port}'],
                             capture_output=True)
        return {'killed': out.returncode == 0, 'manager': 'signal', 'port': self.port}

    def mcp_config(self, host='127.0.0.1'):
        """The MCP client stanza for this module."""
        return {'mcpServers': {'polaris': {'type': 'http',
                                           'url': f'http://{host}:{self.port}/mcp'}}}

    def tools(self):
        """The MCP tool registry this module exposes."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS)}

    # ── self-check ───────────────────────────────────────────────

    def test(self, live=True):
        """Catalog first (needs no key), then the account (needs one)."""
        out = {'key': 'set' if self.api.has_key() else 'missing'}
        if not live:
            return out
        try:
            cat = self.gpus(available_only=False)
            out['catalog'] = {'ok': True, 'types': cat['count']}
        except PolarisError as e:
            out['catalog'] = {'ok': False, **e.dict()}
        if out['key'] == 'missing':
            out['account'] = {'skipped': 'no key'}
            return out
        for name, fn in (('credits', self.credits), ('instances', self.instances),
                         ('account', self.account)):
            try:
                got = fn()
                out[name] = {'ok': True, 'sample': list(got)[:4]}
            except PolarisError as e:
                out[name] = {'ok': False, **e.dict()}
        out['ok'] = all(v.get('ok') for v in out.values() if isinstance(v, dict)
                        and 'ok' in v)
        return out

    def readme(self):
        """This module's skill sheet."""
        try:
            with open(os.path.join(HERE, 'skill.md')) as f:
                return f.read()
        except OSError:
            return self.description


if __name__ == '__main__':
    print(json.dumps(Mod().info(), indent=2, default=str))
