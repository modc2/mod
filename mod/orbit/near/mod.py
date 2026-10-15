"""near — NEAR Protocol behind one mod.

NEAR's personality is its account model: accounts are human-readable names
(`alice.near`) or 64-hex implicit accounts, an account and a contract are the
same object, and access keys carry per-contract permissions — session auth on
chain. This module reads all of it.

    m near/account root.near             # balances, storage, contract flag
    m near/keys root.near                # access keys and what each may do
    m near/contract wrap.near            # callable methods, from the WASM
    m near/abi wrap.near                 # the interface, inferred: kinds, typed args
    m near/view wrap.near ft_metadata    # a free view call
    m near/ft usdt.tether-token.near account_id=root.near
    m near/history root.near             # recent txns, via the indexer
    m near/network                       # height, gas, stake, price

And the write half — a keystore under ~/.mod/near/, never the repo:

    m near/wallet op=generate           # a keypair into the keystore
    m near/create_account myapp.testnet # faucet-funded, ready to sign
    m near/deploy wasm_path=out.wasm account_id=myapp.testnet
    m near/call myapp.testnet set_status args='{"message":"hi"}'
    m near/send bob.testnet 1.5

Writes default to testnet; a non-testnet write refuses without confirm=true;
a write arriving over HTTP needs the token from ~/.mod/near/token. Secret
keys never leave the keystore — no fn, route or tool returns one.

`m near/serve` runs the REST API, console and MCP server on one port.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


def _truthy(v):
    """The shell hands every kwarg over as a string; confirm='false' must not
    unlock a mainnet write."""
    return v is True or str(v).lower() in ('true', '1', 'yes')


class Mod:
    description = """
    near — NEAR Protocol, whole. Reads: accounts by name with staked and
    storage-locked balance broken out, access keys and their per-contract
    permissions, contract methods parsed from the deployed WASM, view calls,
    NEP-141 tokens, history, transactions, blocks, validators. Writes:
    deploy and upgrade contracts, signed change calls, transfers, account
    creation (testnet faucet or sub-accounts), access-key management — keys
    in ~/.mod/near/, testnet by default, mainnet only with confirm=true.
    Twenty MCP tools, a REST API and a console on one port — including a
    directory of the contracts that are ON, verified live against the chain,
    and a self-built census of every contract on the network, scraped block
    by block off the chain itself (live tail + archival backfill, local
    store, no third-party indexer).
    """

    def __init__(self, network=None, rpc=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50910))
        self.base = cfg.get('base_path', '/near')
        self._network, self._rpc = network, rpc

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def client(self, network=None, rpc=None):
        from chain import Client
        return Client(network=network or self._network, rpc=rpc or self._rpc)

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    # ── reading ──────────────────────────────────────────────────

    def account(self, account_id, network=None, rpc=None):
        """Balances (liquid, staked, storage-reserved, USD), contract flag."""
        return self.client(network, rpc).account(account_id)

    balance = account

    def keys(self, account_id, network=None, rpc=None):
        """Access keys and their permissions — NEAR's on-chain session auth."""
        return self.client(network, rpc).keys(account_id)

    def contract(self, account_id, network=None, rpc=None):
        """Callable methods, parsed from the deployed WASM's export section."""
        return self.client(network, rpc).contract(account_id)

    def abi(self, account_id, method=None, refresh=False, deep=True,
            network=None, rpc=None):
        """The contract's interface, inferred: each method's kind (view /
        change / init), typed args with examples, deposit rules, confidence.
        Embedded near-abi when present; otherwise probed off the chain."""
        import mcp
        return mcp.call_tool('near_abi', {'account_id': account_id,
                                          'method': method,
                                          'refresh': _truthy(refresh),
                                          'deep': _truthy(deep),
                                          'network': network or self._network,
                                          'rpc': rpc or self._rpc}, local=True)

    def contracts(self, network=None, rpc=None, refresh=False):
        """The contracts that are ON — the curated directory plus your own
        keystore deploys, each verified live against the chain."""
        import mcp
        return mcp.call_tool('near_contracts',
                             {'network': network or self._network,
                              'rpc': rpc or self._rpc,
                              'refresh': _truthy(refresh)}, local=True)

    def directory(self, q=None, limit=50, offset=0, network=None, **kwargs):
        """Every contract scraped off the chain itself — live tail plus
        archival backfill into a local store; q= filters, limit/offset page."""
        import directory
        return directory.snapshot(network=network or self._network, q=q,
                                  limit=limit, offset=offset)

    def search(self, q, limit=10, network=None, **kwargs):
        """Semantic search over the directory — "stablecoin" finds USDt,
        "lending" finds Burrow, a method name finds contracts exporting it.
        Ranked locally: tf-idf + concept lexicon + trigrams, no service."""
        import search
        return search.search(q, network=network or self._network, limit=limit)

    def view(self, contract, method, args=None, network=None, rpc=None):
        """Call a view method with JSON args. Free — no key, no gas."""
        return self.client(network, rpc).view(contract, method, args)

    def ft(self, contract, account_id=None, network=None, rpc=None):
        """A NEP-141 token: metadata, supply, and a balance if asked."""
        return self.client(network, rpc).ft(contract, account_id)

    def history(self, account_id, limit=25, network=None, rpc=None):
        """Recent transactions, newest first, via the NearBlocks indexer."""
        return self.client(network, rpc).history(account_id, limit)

    def tx(self, hash, sender=None, network=None, rpc=None):
        """One transaction, decoded — actions, outcome, fee."""
        return self.client(network, rpc).tx(hash, sender)

    def block(self, block_id=None, network=None, rpc=None):
        """A block by height or hash, or the latest final one."""
        return self.client(network, rpc).block(block_id)

    def network(self, network=None, rpc=None):
        """Chain id, height, gas price, validators, stake, price."""
        return self.client(network, rpc).status()

    status = network

    def validators(self, limit=30, network=None, rpc=None):
        """The validator set by stake, with uptime and Nakamoto coefficient."""
        return self.client(network, rpc).validators(limit)

    def price(self, **kwargs):
        """NEAR/USD spot, 24h change, market cap."""
        return self.client().price()

    def rpc(self, method, params=None, network=None, rpc=None):
        """Any NEAR JSON-RPC method, raw — the escape hatch."""
        return self.client(network, rpc).rpc(method, params)

    # ── writing — local caller, so no token; the confirm gate stays ──

    def wallet(self, op='status', account_id=None, secret_key=None,
               network='testnet', **kwargs):
        """The keystore: status | generate | import | select | forget."""
        import wallet
        return wallet.wallet(op, account_id, secret_key, network)

    def deploy(self, wasm_path=None, wasm=None, wasm_url=None, account_id=None,
               init_method=None, init_args=None, network='testnet', rpc=None,
               confirm=False, **kwargs):
        """Deploy (or upgrade — state survives) a contract on the signer's
        own account, with an optional init call in the same transaction."""
        import wallet
        return wallet.deploy(wasm, wasm_path, wasm_url, account_id,
                             init_method, init_args, network, rpc,
                             _truthy(confirm))

    def call(self, contract, method, args=None, gas_tgas=None, deposit_near=0,
             account_id=None, network='testnet', rpc=None, confirm=False,
             **kwargs):
        """A signed change call: gas in Tgas, deposit in NEAR."""
        import wallet
        return wallet.call(contract, method, args, gas_tgas, deposit_near,
                           account_id, network, rpc, _truthy(confirm))

    def send(self, to, amount_near, account_id=None, network='testnet',
             rpc=None, confirm=False, **kwargs):
        """Transfer NEAR from a stored account."""
        import wallet
        return wallet.send(to, amount_near, account_id, network, rpc,
                           _truthy(confirm))

    def create_account(self, new_account_id, initial_near=0.1, public_key=None,
                       account_id=None, network='testnet', rpc=None,
                       confirm=False, **kwargs):
        """A named account — testnet faucet for *.testnet, else a funded
        sub-account of the signer."""
        import wallet
        return wallet.create_account(new_account_id, initial_near, public_key,
                                     account_id, network, rpc, _truthy(confirm))

    def key(self, op, public_key=None, contract=None, methods=None,
            allowance_near=None, account_id=None, network='testnet', rpc=None,
            confirm=False, **kwargs):
        """Access keys on the signer's account: add (full or scoped), delete."""
        import wallet
        return wallet.key(op, public_key, contract, methods, allowance_near,
                          account_id, network, rpc, _truthy(confirm))

    # ── surfaces ─────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry, as an agent sees it."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    def mcp_call(self, tool, **args):
        """Invoke one MCP tool directly, without a transport in the way."""
        import mcp
        return mcp.call_tool(tool, args, local=True)

    def mcp_config(self, url=None):
        """Drop-in client config for anything that speaks MCP over HTTP."""
        return {'mcpServers': {'near': {
            'type': 'http', 'url': url or f'http://localhost:{self.port}/mcp'}}}

    def serve(self, port=None, background=False):
        """Run the REST API, the console and the MCP server on one port."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'api.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp'}

    def kill(self, port=None):
        """Stop whatever is holding the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', '-9', pid], capture_output=True)
        return {'port': port, 'killed': pids}

    def test(self, offline=False):
        """Run the test suite. offline=1 skips everything that needs the chain."""
        env = {**os.environ, **({'NEAR_OFFLINE': '1'} if offline else {})}
        out = subprocess.run([sys.executable, '-m', 'pytest', '-q'],
                             cwd=HERE, env=env, capture_output=True, text=True)
        return {'ok': out.returncode == 0,
                'output': out.stdout[-4000:] or out.stderr[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()

    # ── LEARN + BUILD: the workshop (studio.py, lessons.py, tutor.py) ──

    def lessons(self, id=None, **kwargs):
        """The school: every lesson + glossary, or one lesson by id."""
        import lessons
        return lessons.lesson(id) or {'error': f'no lesson {id!r}'} if id else lessons.index()

    lesson = lessons

    def templates(self):
        """Starters a project can be made from (counter, token, commit_reveal, merkle_allowlist)."""
        import studio
        return studio.templates()

    def projects(self):
        """Every project in contracts/: starter, build state, where it shipped."""
        import studio
        return studio.projects()

    def project(self, name):
        """One project: files, parsed methods, walkthrough pinned to lines, artifact."""
        import studio
        return studio.project(name)

    def new(self, name, template='counter', desc=None):
        """Scaffold a project from a starter."""
        import studio
        return studio.new(name, template, desc)

    def build(self, name):
        """cargo build --target wasm32-unknown-unknown --release → .wasm + diagnostics."""
        import studio
        return studio.build(name)

    def verify(self, name):
        """cargo test: the unit tests inside lib.rs, per-test pass/fail + reason."""
        import studio
        return studio.verify(name)

    def check(self, name):
        """build + verify."""
        import studio
        return studio.check(name)

    def vibe(self, name, prompt, model=None):
        """orbit/claudecode edits the crate to the prompt, then build + test."""
        import studio
        return studio.vibe(name, prompt, model)

    def ship(self, name, account_id=None, network='testnet', init_method=None,
             init_args=None, confirm=False, **kwargs):
        """Deploy the built .wasm via the keystore (testnet default; mainnet
        needs confirm=true). Batches the starter's init the first time."""
        import json as _json
        import studio
        if isinstance(init_args, str) and init_args.strip():
            init_args = _json.loads(init_args)
        return studio.ship(name, account_id, network, init_method, init_args,
                           _truthy(confirm))

    def remove(self, name):
        """Delete a project."""
        import studio
        return studio.remove(name)

    def merkle(self, accounts):
        """Root + proofs for the merkle_allowlist starter (sorted-pair sha256)."""
        import studio
        return studio.merkle(accounts)

    def seal(self, contract, who, value, salt):
        """The commit_reveal starter's seal, hex."""
        import studio
        return studio.seal(contract, who, value, salt)

    def toolchain(self):
        """cargo + wasm32 target present? claudecode reachable?"""
        import studio
        return studio.toolchain()

    def agents(self):
        """The agent-protocol card: tutor (open) + builder (token)."""
        import tutor
        return {'agents': list(tutor.AGENTS.values()), 'card': tutor.card(self.base)}

    def ask(self, query, brain='tutor', **context):
        """Ask the tutor from the shell (local = may use brain=claude)."""
        import studio
        import tutor
        j = studio.jobs()
        return tutor.run({'query': query, 'brain': brain, 'context': context},
                         who={'writable': True, 'why': 'local'}, emit=lambda e: None,
                         start_job=j.start, get_job=j.get, agent='tutor',
                         mcp_url=f'http://127.0.0.1:{self.port}/mcp')

    def make(self, query, name=None, template=None, zk=False, vibe=True, model=None):
        """Ask the builder from the shell: scaffold + claudecode + build + test;
        zk=true (or zk words in the ask) forwards to the cairo mod's builder."""
        import studio
        import tutor
        j = studio.jobs()
        body = {'query': query, 'name': name, 'template': template, 'model': model,
                'zk': _truthy(zk), 'vibe': _truthy(vibe)}
        return tutor.run({k: v for k, v in body.items() if v not in (None, '')},
                         who={'writable': True, 'why': 'local'}, emit=lambda e: None,
                         start_job=j.start, get_job=j.get, agent='builder')
