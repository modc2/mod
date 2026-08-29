"""dns — the name layer of the mod protocol.

The protocol's rule is `{host}/{mod}` for a module's app and `{host}/api/{mod}`
for its API. That rule only holds if the host resolves to the box the router
runs on, which is a DNS fact, not an HTTP one. This module is that half: it
derives a zone from the module fleet, serves it authoritatively over UDP and
TCP, resolves any form of a mod address back to the addresses behind it, and
publishes — beside every name — who the protocol attributes that module to.

    m dns/ask "how do I use my own domain"   # plain words in, plain answer out
    m dns/resolve eth                  # a module name → app, API, MCP, A record
    m dns/resolve modc2.com/api/eth    # a URL, a hostname or a bare name, same answer
    m dns/attribution eth              # whose module that is: owner, CID, signed card
    m dns/check modc2.com              # what we hold vs what the internet returns
    m dns/records                      # the system zone, derived + stored
    m dns/plan yourdomain.com          # run the protocol on a host of your own
    m dns/serve                        # API + console + MCP + the name server

Reads are open. Every change is attributed to a signed mod-protocol address:
any signed caller registers and owns their OWN host, while the system zone —
the protocol host itself — belongs to the deployment owner.

The `mod-dns/` directory and `app/` beside this file are the retired Rust +
Next.js prototype, kept for reference; nothing here calls them.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
# `protocol.py` does the same dance in reverse when it needs the real one.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    The DNS layer of the mod protocol. Derives a zone from the module fleet —
    one name per routed module, plus the apex, the wildcard and the
    nameservers — serves it authoritatively on UDP and TCP, and publishes what
    the protocol attributes each module to (owner address, schema CID, and the
    key this box signs its module cards with) as _mod TXT records beside the
    address. Resolves a module name, a hostname, a gateway path or a URL to
    the app, API and MCP addresses behind it, and says when what it holds and
    what the public internet returns disagree. A REST API, a browser console
    and 28 MCP tools on one port.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 5380))
        self.base = cfg.get('base_path', '/dns')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    # ── the protocol's names ─────────────────────────────────────

    def resolve(self, query, type='A'):
        """A module name, hostname, gateway path or URL → every address for it."""
        import actions
        return actions.resolve(query, type)

    where = resolve

    # ── the guide: written for somebody who has never done this ──

    def ask(self, question, token=None):
        """Ask in plain words. Grounded in this box; says when it doesn't know."""
        import actions
        return actions.ask(question, token)

    help_me = ask

    def guide(self, token=None):
        """The checklist for putting the protocol on your own domain, scored."""
        import actions
        return actions.guide(token)

    def explain(self, word, token=None):
        """One piece of DNS vocabulary, in plain English, as it applies here."""
        import actions
        return actions.explain(word, token)

    def glossary(self, token=None):
        """Every word the guide defines, one line each."""
        import actions
        return actions.glossary(token)

    def attribution(self, name=None, verify=False):
        """Who a module is attributed to: owner, schema CID, signed module card."""
        import actions
        return actions.attribution(name, verify)

    who = attribution

    def lookup(self, name, type='A'):
        """What this name server would answer a resolver asking for `name`."""
        import actions
        return actions.lookup(name, type)

    def check(self, name=None, type='A', resolver='1.1.1.1'):
        """The record held here vs the one the public internet returns."""
        import actions
        return actions.check(name, type, resolver)

    def plan(self, host, target=None):
        """How to run the mod protocol on a host you control, step by step."""
        import actions
        return actions.plan(host, target)

    def overview(self, token=None):
        """The whole naming picture: host, zones, modules, listener, changes."""
        import actions
        return actions.overview(token)

    def modules(self):
        """The routed fleet as a name space — every module and its four addresses."""
        import actions
        return actions.modules()

    # ── zones and records ────────────────────────────────────────

    def zones(self):
        """Every zone served here and who owns each one."""
        import actions
        return actions.zones()

    def records(self, zone=None, name=None, type=None):
        """A zone's merged records: what is stored, and what is derived."""
        import actions
        return actions.records(zone, name, type)

    def register(self, zone, token=None, target=None, target_v6=None,
                 modules=True, wildcard=True, note=None):
        """Claim a domain you control and own every record in it."""
        import actions
        return actions.zone_register(token, zone, target, target_v6,
                                     modules, wildcard, note)

    def add(self, name, value, type='A', zone=None, ttl=None, token=None):
        """Write one record into a zone you own."""
        import actions
        return actions.record_set(token, zone, name, type, value, ttl)

    set_record = add

    def remove(self, name, type='A', zone=None, value=None, token=None):
        """Delete one stored record. Derived records are not stored, so shadow
        them with a record of your own instead."""
        import actions
        return actions.record_delete(token, zone, name, type, value)

    def target(self, target=None, zone=None, target_v6=None, token=None):
        """Point a zone's apex, wildcard and module names at one address."""
        import actions
        return actions.zone_target(token, zone, target, target_v6)

    def verify(self, zone=None, resolver='1.1.1.1'):
        """Look for the challenge TXT in the public DNS — proof of delegation."""
        import actions
        return actions.zone_verify(None, zone, resolver)

    # ── the system ───────────────────────────────────────────────

    def host(self, host=None, token=None, sync_router=False):
        """Read the protocol host, or (as owner) repoint it."""
        import actions
        import settings
        if not host:
            return {'host': settings.host(), 'settings': settings.all()}
        return actions.host_set(token, host, sync_router)

    def settings(self, token=None, **kwargs):
        """Read the system settings, or (as owner) change them."""
        import actions
        return actions.settings_set(token, **kwargs)

    def whoami(self, token=None):
        """Your standing here and every operation it lets you run."""
        import actions
        return actions.whoami(token)

    def operations(self, who=None):
        """The catalog: every operation and the standing it requires."""
        import actions
        return actions.operations(who)

    def ops(self, limit=50, op=None, zone=None, actor=None):
        """The change log — every mutation, and the address behind it."""
        import actions
        return actions.ops_log(limit, op, zone, actor)

    def queries(self, limit=50, name=None, rcode=None):
        """Recent resolutions the listener answered."""
        import actions
        return actions.queries(limit, name, rcode)

    def stats(self):
        """Zones, records, modules, listener state, counters, settings."""
        import actions
        return actions.stats()

    status = stats

    # ── surfaces ─────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry, as an agent sees it."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    def mcp_call(self, tool, **args):
        """Invoke one MCP tool directly, without a transport in the way."""
        import mcp
        return mcp.call(tool, args, args.pop('token', None))

    def mcp_config(self, url=None):
        """Drop-in client config for anything that speaks MCP over HTTP."""
        return {'mcpServers': {'dns': {
            'type': 'http', 'url': url or f'http://localhost:{self.port}/mcp'}}}

    def serve(self, port=None, listener=True, background=False):
        """Run the API, the console, the MCP endpoint and the name server.

        One process holds all four: the HTTP surfaces on `port`, and the
        authoritative listener on the DNS port from settings (15353 by
        default — 53 needs root and usually already has something on it).
        """
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port, listener=listener)
        cmd = [sys.executable, os.path.join(HERE, 'api.py'), '--port', str(port)]
        if not listener:
            cmd.append('--no-listener')
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, cwd=HERE)
        import settings
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'dns': f'{settings.get("bind")}:{settings.get("dns_port")} (udp+tcp)'}

    def kill(self, port=None):
        """Stop whatever is holding the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None
