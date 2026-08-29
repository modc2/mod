"""
eth — Ethereum operations and contract deployment, for people and for agents.

One engine, four faces:

    CLI      `m eth/deploy template=token args='["Mod","MOD",18,1000000]'`
    API      api/api.py on :50730 — FastAPI, mod-protocol auth
    console  app/server.py on :50731 at /eth — plain ES modules, no build step
    MCP      mcp.py — the same work as tools, on stdio or POST /mcp

What it does, on any EVM chain in `chains.py` (local anvil, Ethereum, Base,
Arbitrum, Optimism, Polygon, BNB, Avalanche, plus their testnets, plus
whatever you add):

    accounts     keystore-v3 keys, encrypted under a password this module
                 never stores, namespaced by the address that made them
    read         balances, nonces, blocks, transactions, receipts, storage,
                 event logs, ERC-20 metadata, gas prices
    write        send the native currency, call any contract function, move
                 and approve ERC-20s — always after a gas estimate, so a
                 transaction that would revert is never sent
    deploy       Solidity in, an address out: compile with solc (found or
                 fetched), deploy with constructor arguments, and record the
                 ABI, the source and the compiler settings against the
                 deployment so the contract stays usable a year later
    templates    nine self-contained contracts worth deploying — token, NFT,
                 multisig, escrow, splitter, registry, anchor, vault, counter

Two safety properties hold everywhere, including in the MCP tools an agent
drives unattended:

    a non-testnet write needs `confirm=true` on the request that spends
    a key is only used while it is unlocked, for as long as you unlocked it

Usage (Python):
    import mod as m
    eth = m.mod('eth')()
    eth.account('dev', password='…')            # make a key
    eth.balance('dev', network='sepolia')
    eth.deploy(account='dev', template='counter', args=[0], password='…')

Usage (CLI):
    m eth/networks
    m eth/templates
    m eth/balance address=0x… network=base
    m eth/deploy account=dev template=token args='["Mod","MOD",18,1000000]'
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DIR = Path(__file__).resolve().parent
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))

STATE = Path(os.path.expanduser(os.environ.get('ETH_DIR', '~/.mod/eth')))


class Mod:
    """Ethereum operations and contract deployment."""

    description = 'Ethereum reads, transfers, and Solidity contract deployment ' \
                  'across EVM chains — API, console, CLI and MCP over one engine.'

    def __init__(self, network: str = None, account: str = None,
                 owner: str = None, **kwargs):
        # `owner` is the identity everything is scoped to. On the CLI there is
        # nobody to sign, so this box's own auth key stands in — the same
        # address the API would derive from a token that key minted.
        self.network = network or os.environ.get('ETH_NETWORK', 'local')
        self.account = account
        self._owner = owner
        STATE.mkdir(parents=True, exist_ok=True)

    # ── identity ──────────────────────────────────────────────────

    @property
    def owner(self) -> str:
        if self._owner:
            return self._owner.lower()
        env = os.environ.get('ETH_OWNER')
        if env:
            return env.lower()
        try:
            import protocol
            self._owner = str(protocol.auth().key.key_address).lower()
        except Exception:
            try:
                import protocol
                self._owner = str(protocol.auth().key.address).lower()
            except Exception:
                self._owner = 'local'
        return self._owner

    def whoami(self, **kw) -> dict:
        """Which address this CLI acts as, and what it owns."""
        import ledger
        import wallet
        return {'owner': self.owner, 'accounts': wallet.listing(self.owner),
                'counts': ledger.counts(self.owner), 'network': self.network}

    # ── the default face ──────────────────────────────────────────

    def forward(self, **kwargs):
        return self.status(**kwargs)

    def status(self, network: str = None, **kw) -> dict:
        """Is everything this module needs actually here."""
        import chains
        import compiler
        import identity
        import ledger
        import wallet
        return {
            'module': 'eth',
            'network': chains.reachable(network or self.network),
            'networks': len(chains.summary()),
            'accounts': len(wallet.listing(self.owner)),
            'solc': compiler.status(),
            'templates': __import__('catalog').names(),
            'auth': identity.status(),
            'owner': self.owner,
            'index': ledger.counts(self.owner),
            'state': str(STATE),
        }

    # ── networks ──────────────────────────────────────────────────

    def networks(self, check: bool = False, **kw) -> dict:
        """Every chain this deployment can reach (check=1 to ping each)."""
        import chains
        rows = chains.summary()
        if check:
            rows = [chains.reachable(row['name']) for row in rows]
        return {'default': self.network, 'networks': rows}

    def network_add(self, name: str, rpc: str, chain_id: int = None,
                    testnet: bool = True, explorer: str = None,
                    currency: str = 'ETH', **kw) -> dict:
        """Teach this deployment about another chain."""
        import chains
        return chains.add(name, rpc, chain_id, testnet, explorer, currency)

    def network_remove(self, name: str, **kw) -> dict:
        import chains
        return {'removed': chains.remove(name), 'name': name}

    # ── accounts ──────────────────────────────────────────────────

    def accounts(self, **kw) -> dict:
        """Your keys: names, addresses, and which are unlocked."""
        import wallet
        return {'owner': self.owner, 'accounts': wallet.listing(self.owner)}

    def account(self, name: str, password: str, mnemonic: bool = False, **kw) -> dict:
        """Make a new account. A mnemonic, if asked for, is shown once."""
        import wallet
        return wallet.create(self.owner, name, password, mnemonic=mnemonic)

    def import_account(self, name: str, password: str, secret: str, **kw) -> dict:
        """Bring in a private key or a BIP-39 mnemonic."""
        import wallet
        return wallet.import_key(self.owner, name, password, secret)

    def export_account(self, name: str, password: str, confirm: bool = False, **kw) -> dict:
        """The raw private key — asks twice, because it is the whole account."""
        import wallet
        if not confirm:
            return {'error': 'pass confirm=1 — this prints a key that owns funds'}
        return wallet.export(self.owner, name, password)

    def delete_account(self, name: str, confirm: bool = False, **kw) -> dict:
        import wallet
        if not confirm:
            return {'error': 'pass confirm=1 — without a backup this is final'}
        return wallet.delete(self.owner, name)

    def unlock(self, name: str, password: str, ttl: int = 300, **kw) -> dict:
        """Hold a key in memory for a while so a batch of work needs one password."""
        import wallet
        return wallet.unlock(self.owner, name, password, ttl)

    def lock(self, name: str = None, **kw) -> dict:
        import wallet
        return wallet.lock(self.owner, name)

    def sign(self, name: str, message: str, password: str = None, **kw) -> dict:
        """EIP-191 personal_sign — the same shape a browser wallet makes."""
        import wallet
        return wallet.sign_message(self.owner, name, message, password)

    def verify(self, message: str, signature: str, **kw) -> dict:
        import wallet
        return wallet.verify_message(message, signature)

    # ── reading ───────────────────────────────────────────────────

    def balance(self, address: str = None, network: str = None,
                token: str = None, **kw) -> dict:
        """Native (or ERC-20) balance of an address, an account name or ENS."""
        import ops
        return ops.balance(address or self.account or '', network or self.network,
                           self.owner, token)

    def portfolio(self, address: str = None, networks: List[str] = None, **kw) -> dict:
        """Your accounts across several chains at once."""
        import ops
        return ops.portfolio(self.owner, address, networks)

    def block(self, number: Any = 'latest', network: str = None,
              full: bool = False, **kw) -> dict:
        import ops
        return ops.block(number, network or self.network, full)

    def tx(self, hash: str, network: str = None, **kw) -> dict:
        """A transaction and its receipt, or that it is still pending."""
        import ops
        return ops.transaction(hash, network or self.network)

    def wait(self, hash: str, network: str = None, timeout: int = 180, **kw) -> dict:
        import ops
        return ops.wait(hash, network or self.network, timeout)

    def gas(self, network: str = None, **kw) -> dict:
        """What a transaction costs on this chain right now."""
        import ops
        return ops.fees(network or self.network)

    def nonce(self, address: str, network: str = None, **kw) -> dict:
        import ops
        return ops.nonce(address, network or self.network, self.owner)

    def code(self, address: str, network: str = None, **kw) -> dict:
        """Is there a contract there, and does this box know its ABI."""
        import ops
        return ops.code(address, network or self.network, self.owner)

    def logs(self, address: str = None, network: str = None,
             from_block: Any = 'latest', to_block: Any = 'latest',
             topics: List[Any] = None, **kw) -> dict:
        """Raw event logs, decoded when the ABI is known."""
        import ops
        return ops.logs(network or self.network, address, from_block, to_block,
                        topics, self.owner)

    def estimate(self, to: str = None, data: str = None, value: Any = 0,
                 network: str = None, sender: str = None, **kw) -> dict:
        import ops
        return ops.estimate(to, data, value, network or self.network, sender, self.owner)

    def call(self, to: str, data: str, network: str = None, sender: str = None, **kw) -> dict:
        """eth_call with hand-made calldata, for when there is no ABI at all."""
        import ops
        return ops.call_raw(to, data, network or self.network, sender, self.owner)

    # ── writing ───────────────────────────────────────────────────

    def send(self, to: str, value: Any, account: str = None, network: str = None,
             password: str = None, confirm: bool = False, wait: bool = True, **kw) -> dict:
        """Move the native currency. `confirm=1` is required off a testnet."""
        import ops
        return ops.send(self.owner, account or self.account, to, value,
                        network or self.network, password, confirm=confirm,
                        wait_for=wait)

    def send_raw(self, signed: str, network: str = None, confirm: bool = False, **kw) -> dict:
        import ops
        return ops.send_raw(signed, network or self.network, self.owner, confirm)

    # ── contracts ─────────────────────────────────────────────────

    def templates(self, name: str = None, source: bool = False, **kw) -> dict:
        """The contracts that ship with this module."""
        import catalog
        if name:
            out = catalog.describe(name, compile_it=True)
            if source:
                out['source'] = catalog.source(name)
            return out
        return {'templates': catalog.listing()}

    def compile(self, source: str = None, path: str = None, template: str = None,
                solc: str = None, optimize: bool = True, runs: int = 200, **kw) -> dict:
        """Solidity in, ABI and bytecode out. Nothing is sent anywhere."""
        import catalog
        import compiler
        text = source
        name = 'Contract.sol'
        if template:
            text, name = catalog.source(template), f'{template}.sol'
        elif path:
            text, name = Path(path).expanduser().read_text(), Path(path).name
        if not text:
            return {'error': 'give me source=, path= or template='}
        out = compiler.compile_sources({name: text}, version=solc,
                                       optimize=optimize, runs=runs)
        # The CLI prints this; a full bytecode blob per contract is unreadable.
        for contract in out['contracts']:
            contract['bytecode'] = contract['bytecode'][:80] + '…'
            contract.pop('deployed_bytecode', None)
        return out

    def deploy(self, account: str = None, template: str = None, source: str = None,
               path: str = None, contract: str = None, args: Any = None,
               network: str = None, password: str = None, value: Any = 0,
               solc: str = None, optimize: bool = True, runs: int = 200,
               name: str = None, confirm: bool = False, note: str = None,
               wait: bool = True, **kw) -> dict:
        """Compile and deploy in one step; the ABI is kept against the address."""
        import catalog
        import ops
        text = source
        if template:
            text = catalog.source(template)
            name = name or catalog.describe(template)['contract']
        elif path:
            text = Path(path).expanduser().read_text()
        if isinstance(args, str):
            args = json.loads(args)
        return ops.deploy(self.owner, account or self.account,
                          network=network or self.network, source=text,
                          contract=contract, args=args or [], value=value,
                          password=password, solc=solc, optimize=optimize,
                          runs=runs, name=name, confirm=confirm, note=note,
                          wait_for=wait)

    def contracts(self, network: str = None, **kw) -> dict:
        """Everything you deployed here, plus every ABI you attached."""
        import ledger
        return {'deployed': [{k: v for k, v in row.items()
                              if k not in ('abi', 'bytecode', 'source')}
                             for row in ledger.deployments(self.owner, network)],
                'attached': [{k: v for k, v in row.items() if k != 'abi'}
                             for row in ledger.attached(self.owner, network)]}

    def contract(self, address: str, network: str = None, **kw) -> dict:
        """What can be done with a contract: its reads, writes and events."""
        import ops
        return ops.interface(address, network or self.network, owner=self.owner)

    def attach(self, address: str, abi: Any, network: str = None,
               name: str = None, **kw) -> dict:
        """Teach this box the ABI of a contract somebody else deployed."""
        import chains
        import ledger
        spec = chains.resolve(network or self.network)
        if isinstance(abi, str) and abi.strip().startswith('['):
            abi = json.loads(abi)
        elif isinstance(abi, str):
            abi = json.loads(Path(abi).expanduser().read_text())
        row = ledger.attach(self.owner, address, abi, spec['name'],
                            spec.get('chain_id'), name)
        return {k: v for k, v in row.items() if k != 'abi'} | {'functions': len(abi)}

    def read(self, address: str, function: str, args: Any = None,
             network: str = None, abi: Any = None, **kw) -> dict:
        """A view call: free, keyless, and changes nothing."""
        import ops
        if isinstance(args, str):
            args = json.loads(args)
        return ops.read(address, function, args or [], network or self.network,
                        abi, self.owner)

    def write(self, address: str, function: str, args: Any = None,
              account: str = None, network: str = None, value: Any = 0,
              password: str = None, abi: Any = None, confirm: bool = False,
              wait: bool = True, **kw) -> dict:
        """A state-changing call. Estimated first, so a revert never gets sent."""
        import ops
        if isinstance(args, str):
            args = json.loads(args)
        return ops.write(self.owner, account or self.account, address, function,
                         args or [], network or self.network, abi, value,
                         password, confirm=confirm, wait_for=wait)

    # ── tokens ────────────────────────────────────────────────────

    def token(self, address: str, network: str = None, holder: str = None, **kw) -> dict:
        """ERC-20 metadata, and a holder's balance if you name one."""
        import ops
        out = ops.token_info(address, network or self.network, self.owner)
        if holder:
            out['holder'] = ops.token_balance(address, holder,
                                              network or self.network, self.owner)
        return out

    def transfer(self, token: str, to: str, amount: Any, account: str = None,
                 network: str = None, password: str = None,
                 confirm: bool = False, **kw) -> dict:
        """Move an ERC-20. Amounts are in whole tokens unless you say wei."""
        import ops
        return ops.token_transfer(self.owner, account or self.account, token, to,
                                  amount, network or self.network, password, confirm)

    def approve(self, token: str, spender: str, amount: Any, account: str = None,
                network: str = None, password: str = None,
                confirm: bool = False, **kw) -> dict:
        import ops
        return ops.token_approve(self.owner, account or self.account, token, spender,
                                 amount, network or self.network, password, confirm)

    # ── projects: write it, keep it, share it ─────────────────────
    #
    # On the CLI the token is minted from this box's own key, so the store
    # files the project under the same address `whoami` reports.

    def _token(self, token: str = None) -> str:
        if token:
            return token
        import store_link
        return store_link.local_token()

    def projects(self, limit: int = 100, **kw) -> dict:
        """Your contract projects, newest first."""
        import projects as P
        return {'projects': P.listing(self.owner, limit),
                'counts': P.counts(self.owner)}

    def project(self, name: str, **kw) -> dict:
        """One project: its files, its tests, its CIDs."""
        import projects as P
        return P.get(self.owner, name)

    def save(self, name: str = None, source: str = None, path: str = None,
             files: Any = None, entry: str = None, project: str = None,
             note: str = None, public: bool = False, origin_cid: str = None,
             token: str = None, **kw) -> dict:
        """Write a project to the store and keep its CID.

        `m eth/save name=Counter path=./Counter.sol`
        """
        import projects as P
        if path and not source:
            source = Path(os.path.expanduser(path)).read_text()
            entry = entry or Path(path).name
        if isinstance(files, str):
            files = json.loads(files)
        return P.save(self.owner, self._token(token), name=name, files=files,
                      source=source, entry=entry, project=project, note=note,
                      public=public, origin_cid=origin_cid)

    def share(self, project: str, token: str = None, **kw) -> dict:
        """Make a project public in the store; the CID is the share link."""
        import projects as P
        return P.share(self.owner, self._token(token), project)

    def unshare(self, project: str, token: str = None, **kw) -> dict:
        """Make it private again. Anyone already holding the CID keeps it."""
        import projects as P
        return P.unshare(self.owner, self._token(token), project)

    def open(self, cid: str, token: str = None, **kw) -> dict:
        """Read a shared project out of the store by CID — no account needed."""
        import projects as P
        return P.open_bundle(token or None, cid)

    def fork(self, cid: str, name: str = None, token: str = None, **kw) -> dict:
        """Copy somebody's shared project into your own workspace."""
        import projects as P
        return P.fork(self.owner, self._token(token), cid, name)

    def forget_project(self, project: str, from_store: bool = False,
                       token: str = None, **kw) -> dict:
        """Drop a project from your index (and, if asked, from the store)."""
        import projects as P
        return P.delete(self.owner, project, self._token(token), from_store)

    def store(self, token: str = None, **kw) -> dict:
        """Where storage stands: the store module, and what is blocking you."""
        import store_link
        return store_link.LINK.status(token or self._token())

    def store_terms(self, token: str = None, **kw) -> dict:
        """Sign-accept the store's terms so uploads are allowed."""
        import store_link
        return store_link.LINK.accept_terms(token or self._token())

    # ── tests: put it on a chain and push it ──────────────────────

    def test(self, project: str = None, source: str = None, path: str = None,
             suites: Any = None, suite_path: str = None, account: str = None,
             password: str = None, network: str = None, contract: str = None,
             args: Any = None, address: str = None, confirm: bool = False,
             token: str = None, **kw) -> dict:
        """Deploy to a testnet, run the suite, report every case.

        `m eth/test project=counter-demo account=dev password=… network=sepolia`

        With no suite, every free getter on the ABI is called — proof the
        deploy works, not proof the contract behaves.
        """
        import harness
        if path and not source:
            source = Path(os.path.expanduser(path)).read_text()
        if suite_path and suites is None:
            suites = json.loads(Path(os.path.expanduser(suite_path)).read_text())
        if isinstance(args, str):
            args = json.loads(args)
        return harness.run(self.owner, account or self.account or 'default',
                           network=network or self.network, source=source,
                           project=project, contract=contract, suites=suites,
                           args=args, password=password, address=address,
                           confirm=confirm, token=self._token(token))

    def generate_tests(self, project: str = None, source: str = None,
                       contract: str = None, **kw) -> dict:
        """A starter suite read off the ABI — every free getter, no guesses."""
        import compiler
        import harness
        import projects as P
        files = {'Contract.sol': source} if source else \
            P.get(self.owner, project)['files']
        compiled = compiler.compile_sources(files)
        deployable = [c for c in compiled['contracts'] if c['deployable']]
        chosen = next((c for c in deployable if c['name'] == contract),
                      deployable[0] if deployable else None)
        if chosen is None:
            raise ValueError('this source has nothing deployable in it')
        return harness.generate(chosen['abi'], name=chosen['name'])

    def tests(self, limit: int = 30, project: str = None, **kw) -> dict:
        """Past test runs, newest first."""
        import harness
        return {'runs': harness.runs(self.owner, limit, project)}

    def report(self, run_id: int, **kw) -> dict:
        """One test run in full — every case, every transaction hash."""
        import harness
        return harness.report(self.owner, run_id)

    # ── history ───────────────────────────────────────────────────

    def history(self, network: str = None, limit: int = 50, **kw) -> dict:
        """Every transaction this module sent for you, and how each ended."""
        import ledger
        return {'txs': ledger.txs(self.owner, network, limit)}

    # ── the MCP server ────────────────────────────────────────────

    def _mcp(self):
        spec = importlib.util.spec_from_file_location('eth_mcp', DIR / 'mcp.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def mcp(self, url: str = None, **kw) -> dict:
        """The MCP server described: transports, auth, tool names, client config."""
        doc = self._mcp().describe(url)
        doc['tools'] = [{'name': t['name'], 'auth': t['auth'],
                         'summary': t['description'].split('.')[0] + '.'}
                        for t in doc['tools']]
        return doc

    def mcp_tools(self, name: str = None, **kw) -> dict:
        """The full tool schemas — what `tools/list` returns, one or all."""
        tools = self._mcp().describe()['tools']
        if name:
            tools = [t for t in tools if t['name'] in (name, f'eth_{name}')]
            if not tools:
                return {'error': f'no such tool: {name}'}
        return {'count': len(tools), 'tools': tools}

    def mcp_call(self, tool: str, **kw):
        """Run one MCP tool with this box's own identity (what stdio does)."""
        mcp = self._mcp()
        name = tool if tool in mcp.TOOLS else f'eth_{tool}'
        try:
            return mcp.call_tool(name, kw, owner=self.owner)
        except Exception as e:
            return {'error': f'{name}: {e}'}

    # ── the two services ──────────────────────────────────────────

    def serve(self, no_app: bool = False, no_api: bool = False, **kw) -> dict:
        """Launch the API and the console under pm2 (eth-api / eth-app)."""
        import subprocess
        args = ['bash', str(DIR / 'serve.sh')]
        if no_app:
            args.append('--no-app')
        if no_api:
            args.append('--no-api')
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=300)
        except Exception as e:
            return {'error': str(e)}
        cfg = json.loads((DIR / 'config.json').read_text())
        return {'pm2': ['eth-api', 'eth-app'], 'api': cfg['urls']['api'],
                'app': cfg['urls']['app'], 'returncode': out.returncode,
                'stdout': out.stdout[-2000:], 'stderr': out.stderr[-2000:]}

    def stop(self, **kw) -> dict:
        import subprocess
        out = subprocess.run(['bash', str(DIR / 'serve.sh'), 'stop'],
                             capture_output=True, text=True)
        return {'returncode': out.returncode, 'stdout': out.stdout[-2000:]}

    def app(self, **kw) -> dict:
        """Launch only the console."""
        return self.serve(no_api=True)

    def api(self, **kw) -> dict:
        """Launch only the API."""
        return self.serve(no_app=True)

    def readme(self, **kw):
        path = DIR / 'README.md'
        return path.read_text() if path.exists() else None
