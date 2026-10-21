import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src')
if SRC not in sys.path:
    # Appended, not prepended: this file would shadow the protocol's own
    # `mod` package for anything importing us afterwards.
    sys.path.append(SRC)


class Mod:
    description = """A post-quantum L1 whose entire state machine is a market
    in key/value space. ML-DSA signatures, ML-KEM key exchange, SHA3-256
    commitments — no elliptic curve anywhere. A key maps to a value, usually a
    32-byte hash of something kept off-chain, and holding that pair costs
    money continuously: write gas per byte, witness gas per signature byte
    (an ML-DSA signature is 2420 bytes and the chain bills for it), and rent
    per byte-hour against a prepaid escrow. Expired entries pay whoever sweeps
    them. REST, the browser console and 22 MCP tools on one port, off the
    same functions."""
    path = HERE

    # ── the surfaces ──────────────────────────────────────────────

    def serve(self, port=None, bind=None):
        """Run the node: REST + console + MCP on one port, plus the block
        loop. This call blocks."""
        import api
        return api.serve(int(port or api.PORT), bind=bind)

    def mcp(self, request: dict):
        """One MCP JSON-RPC message in, one response out."""
        import mcp as mcpsrv
        return mcpsrv.handle(request)

    def call(self, tool: str, **kwargs):
        """Run any pq_* tool by name — the same door REST and MCP use."""
        import mcp as mcpsrv
        return mcpsrv.call_tool(tool if tool.startswith('pq_')
                                else 'pq_' + tool, kwargs)

    def tools(self):
        """The MCP tool registry."""
        import mcp as mcpsrv
        return mcpsrv.tool_list()

    # ── the chain, as direct calls ────────────────────────────────

    def forward(self, **kwargs):
        """Default entry point: the chain tip."""
        return self.head()

    def head(self):
        return self.call('head')

    def market(self, **kw):
        return self.call('market', **kw)

    def keys(self, **kw):
        return self.call('keys', **kw)

    def get(self, key):
        return self.call('get', key=key)

    def quote(self, key, **kw):
        return self.call('quote', key=key, **kw)

    def set(self, key, **kw):
        return self.call('set', key=key, **kw)

    def prove(self, key):
        return self.call('prove', key=key)

    def check(self, key, **kw):
        return self.call('check', key=key, **kw)

    def wallet(self, action='list', **kw):
        return self.call('wallet', action=action, **kw)

    def faucet(self, **kw):
        return self.call('faucet', **kw)

    def account(self, **kw):
        return self.call('account', **kw)

    def transfer(self, to, amount, **kw):
        return self.call('transfer', to=to, amount=amount, **kw)

    def verify(self, signatures=False):
        return self.call('verify', signatures=signatures)

    def mine(self, force=False):
        return self.call('mine', force=force)

    # ── the vault: sealed HERE, before anything leaves this machine ──

    def vault_put(self, path, key=None, passphrase=None, wallet=None,
                  days=None, url=None):
        """Encrypt a local file on this machine (AES-256-GCM, PQV1), upload
        only the ciphertext, and optionally anchor its cid at chain key
        `key`. url= targets a remote node's API (default: this process).
        Returns the share secret — it never goes over the wire."""
        import base64
        import vault as V
        with open(os.path.expanduser(path), 'rb') as f:
            data = f.read()
        blob, secret = V.seal(data, name=os.path.basename(path),
                              passphrase=passphrase)
        args = {'action': 'put', 'blob': base64.b64encode(blob).decode()}
        if key:
            args.update(key=key, wallet=wallet, days=days)
        args = {k: v for k, v in args.items() if v is not None}
        out = self._remote('vault', args, url) if url else \
            self.call('vault', **args)
        out['secret'] = secret or '(your passphrase)'
        out['share'] = f'pqv1:{out["cid"]}#{secret}' if secret else None
        return out

    def vault_get(self, cid=None, key=None, secret=None, passphrase=None,
                  out=None, url=None):
        """Fetch ciphertext by cid or chain key and decrypt it HERE.
        secret= is the key (or a whole pqv1:<cid>#<key> share string)."""
        import base64
        import vault as V
        if secret and secret.startswith('pqv1:'):
            cid, secret = secret[5:].split('#', 1)
        args = {'action': 'get', 'cid': cid, 'key': key}
        args = {k: v for k, v in args.items() if v}
        r = self._remote('vault', args, url) if url else \
            self.call('vault', **args)
        meta, data = V.open_(base64.b64decode(r['blob']), key=secret,
                             passphrase=passphrase)
        if out:
            with open(os.path.expanduser(out), 'wb') as f:
                f.write(data)
            return {**meta, 'written': out, 'cid': r['cid']}
        return {**meta, 'cid': r['cid'],
                'data': data.decode('utf-8', 'replace')}

    def contract(self, action='list', **kw):
        """Store / read / run smart contracts (pq_contract)."""
        return self.call('contract', action=action, **kw)

    def contract_run(self, name, input='', wallet=None, anchor=True, **kw):
        """Run a stored wasm contract OFF chain on this node, sign a pqr1
        receipt with the wallet and anchor it at contract/<name>/receipt.
        input= hex bytes (see `contract examples` for each example's ABI)."""
        return self.call('contract', action='run', name=name, input=input,
                         wallet=wallet, anchor=anchor, **kw)

    def contract_verify(self, name=None, receipt=None, **kw):
        """Re-run a receipt (the anchored latest for name=, or receipt=cid)
        and compare: signature, replay, owner, anchor."""
        return self.call('contract', action='verify', name=name,
                         receipt=receipt, **kw)

    def proofs(self):
        """Proof systems for off-chain state machines: replay | wasm (a
        custom pqp1 verifier) | quorum — plus verifiers and machines."""
        return self.call('contract', action='proofs')

    def contract_submit(self, name, input='', state_out_b64='', wallet=None,
                        **kw):
        """Submit a transition computed elsewhere: accepted by the machine's
        proof policy (proof_b64= / attestations=), signed and anchored."""
        return self.call('contract', action='submit', name=name, input=input,
                         state_out_b64=state_out_b64, wallet=wallet, **kw)

    def contract_examples(self):
        """The bundled pqc1 example contracts (counter, ledger) and how to
        drive them."""
        return self.call('contract', action='examples')

    def _remote(self, route, args, url):
        import json
        import urllib.request
        req = urllib.request.Request(
            url.rstrip('/') + '/' + route, data=json.dumps(args).encode(),
            headers={'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    # ── the agent: plain English → the same tools ─────────────────

    def ask(self, query='help', agent=None, brain='auto', **kw):
        """Ask the chain in plain words — "set hello to world for 2 days
        then get hello". A local call is the operator, so it may write."""
        import agent as pqagent
        return pqagent.run(query, agent=agent, brain=brain, can_write=True, **kw)

    def agents(self):
        """The agent roster — the fleet's agent contract (GET /agents)."""
        import agent as pqagent
        return pqagent.agents()

    # ── housekeeping ──────────────────────────────────────────────

    def info(self):
        """Module card: what it is, the tip, every way in."""
        import api
        return api.info()

    def test(self):
        """Run the test suite."""
        import subprocess
        r = subprocess.run([sys.executable, '-m', 'pytest',
                            os.path.join(HERE, 'tests'), '-q'],
                           capture_output=True, text=True, cwd=HERE)
        return {'ok': r.returncode == 0,
                'out': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None
