"""
defi — build DeFi protocols by wiring reusable Solidity modules together.

The premise: a lending market, a yield vault and a liquidity mine are not
monoliths, they are the same handful of parts wired differently. So this module
ships those parts as BLOCKS — audited-shaped, self-contained Solidity contracts
with typed ports — and a canvas for connecting them. Drag a token into a
vault's asset port, hang a strategy off the vault, point a lending pool at a
price feed, and the server type-checks the composition, compiles it with solc,
and hands back an ordered deployment plan.

Nothing here holds a key. The plan is signed and broadcast by the browser
wallet, so the worst this service can do is PROPOSE a transaction you still
have to approve.

Composability, the mod way:
  * blocks are data — blocks/catalog.json plus a .sol file. Adding one needs no
    Rust and no frontend change.
  * designs are content-addressed. `publish` gives you a CID; anyone on the
    fleet can `import` it and get the same diagram.
  * prompts come from the AGENT protocol. The agent mod already owns a shared,
    CID-pinned prompt library, so this console browses THAT rather than growing
    a second one — and `compose` turns a sentence into a validated graph.
  * yield is data too. `yields` is DefiLlama's index of ~17k pools — the live
    APR of every DeFi protocol — normalised so `apy_base` (fees) and
    `apy_reward` (emissions) never blur into one flattering number. Pick a row
    and `treasury_choose` writes it into a treasury that pays out every Friday
    12:00 EST, split by BLOC — BlocTime's own window and BlocTime's own
    balances, read from the module that owns them.
  * trading works the same way. A protocol you deploy needs liquidity and a
    price, so this module has a DEX desk — quote and swap on Solana, Ethereum,
    Base and Bittensor — and every one of those trades is executed by the mod
    that already owns that chain: `eth` signs the Uniswap V3 call, `solana`
    signs the Jupiter route, `bt` stakes into the dTAO pool. No second wallet,
    no second RPC stack, no key in this process.

Architecture: Rust axum API on :50500 (catalog, validation, solc, planning,
MCP) and a Next.js canvas on :50501 under /defi.

CLI:
    m defi                         # info
    m defi/blocks                  # the block catalog
    m defi/block vault             # one block, with its Solidity source
    m defi/templates               # starter protocols
    m defi/validate <graph.json>   # type-check a composition
    m defi/plan <graph.json>       # …and get the deployment plan
    m defi/protocols               # saved designs
    m defi/prompts                 # browse the agent protocol's prompt library
    m defi/compose "a stablecoin vault that farms"   # sentence -> graph
    m defi/yield_protocols         # the APR for each DeFi protocol, live
    m defi/yields chain=Base organic=1   # …by pool, fees not emissions
    m defi/treasury                # the treasury: allocations, clock, contract
    m defi/treasury_choose pool=<id> amount=1000 term_weeks=12
    m defi/treasury_preview        # next Friday: the pot and who splits it
    m defi/treasury_lock <id> --account=dev --confirm=1   # make it real
    m defi/venues                  # where this desk can trade, and what is up
    m defi/quote base ETH USDC 0.1 # what a trade would really get you
    m defi/swap base ETH USDC 0.1 --account=trader --confirm=1   # trade it
    m defi/serve                   # run api + app
    m defi/status
"""
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import mod as m

API_PORT = 50500
APP_PORT = 50501
API = os.environ.get('DEFI_API', f'http://localhost:{API_PORT}')
HERE = os.path.dirname(os.path.abspath(__file__))


def _qs(params):
    """Drop the keys nobody set, so the API sees an absent filter as absent."""
    live = {k: v for k, v in params.items() if v not in (None, '', False)}
    return ('?' + urllib.parse.urlencode(live)) if live else ''


class Mod:
    description = ('defi — drag-and-drop composer for Ethereum DeFi protocols: reusable Solidity '
                   'blocks with typed ports, graph type-checking, solc compilation, wallet-signed '
                   'deployment plans, CID-shared designs, and AI composition over the agent '
                   'protocol prompt library')
    path = HERE

    # --- plumbing -----------------------------------------------------------

    def _call(self, route, method='GET', body=None, token=None, timeout=180):
        url = f'{API}{route}'
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('content-type', 'application/json')
        if token:
            req.add_header('authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:400]
            try:
                return {'error': json.loads(detail).get('error', detail), 'status': e.code}
            except Exception:
                return {'error': detail, 'status': e.code}
        except urllib.error.URLError as e:
            return {'error': f'api unreachable at {API} ({e.reason}) — try `m defi/serve`'}

    @staticmethod
    def _graph(graph):
        """Accept a dict, a JSON string, or a path to a .json file."""
        if isinstance(graph, dict):
            return graph
        if isinstance(graph, str):
            expanded = m.abspath(graph)
            if os.path.exists(expanded):
                return json.loads(m.get_text(expanded))
            return json.loads(graph)
        raise ValueError('graph must be a dict, JSON string, or path to a .json file')

    # --- protocol surface ---------------------------------------------------

    def forward(self, **kwargs):
        """Default entry point — a null call returns info."""
        return self.info()

    def info(self):
        """What this module is and whether its services are up."""
        health = self._call('/health')
        return {
            'name': 'defi',
            'description': self.description,
            'api': API,
            'app': f'http://localhost:{APP_PORT}/defi',
            'mcp': f'{API}/mcp',
            'health': health,
            'blocks': health.get('blocks'),
            'agent': self._call('/agent/status').get('reachable'),
        }

    def health(self):
        """API liveness."""
        return self._call('/health')

    def blocks(self):
        """The reusable block catalog — every block, its ports and its params."""
        cat = self._call('/catalog')
        if 'error' in cat:
            return cat
        return [
            {
                'id': b['id'],
                'name': b['name'],
                'category': b['category'],
                'summary': b['summary'],
                'provides': b.get('provides', []),
                'needs': [f"{i['id']}:{i['type']}" + ('' if i.get('required') else '?')
                          for i in b.get('inputs', [])],
            }
            for b in cat.get('blocks', [])
        ]

    def block(self, id):
        """One block in full: metadata, Solidity source, compiled ABI + bytecode."""
        return self._call(f'/catalog/{id}')

    def catalog(self):
        """The raw catalog document (blocks, port types, templates)."""
        return self._call('/catalog')

    def templates(self):
        """Starter compositions you can drop straight onto the canvas."""
        return self._call('/templates').get('templates', [])

    def validate(self, graph):
        """Type-check a composition. Returns issues plus the deployment order."""
        return self._call('/validate', 'POST', {'graph': self._graph(graph)})

    def plan(self, graph):
        """Compile and return the ordered deployment plan for a composition."""
        return self._call('/plan', 'POST', {'graph': self._graph(graph)})

    def compile_status(self):
        """Which solc compiled the catalog, and whether it succeeded."""
        return self._call('/compile/status')

    # --- saved designs ------------------------------------------------------

    def protocols(self):
        """Every saved protocol design on this node."""
        return self._call('/protocols').get('protocols', [])

    def protocol(self, id):
        """One saved design, with its graph and recorded deployments."""
        return self._call(f'/protocols/{id}')

    def save(self, graph, name=None, id=None, token=None):
        """Save a design (needs a signed-in wallet token)."""
        body = {'graph': self._graph(graph)}
        if name:
            body['name'] = name
        if id:
            body['id'] = id
        return self._call('/protocols', 'POST', body, token=token)

    def publish(self, id, token=None):
        """Content-address a design and get its CID for sharing."""
        return self._call(f'/protocols/{id}/publish', 'POST', {}, token=token)

    def import_protocol(self, cid, token=None):
        """Import a design someone shared by CID."""
        return self._call('/protocols/import', 'POST', {'cid': cid}, token=token)

    # --- agent protocol -----------------------------------------------------

    def prompts(self, token=None):
        """Browse the agent protocol's shared prompt library."""
        return self._call('/agent/prompts', token=token).get('prompts', [])

    def prompt(self, id, token=None):
        """Retrieve one prompt from the agent protocol by id or CID."""
        return self._call(f'/agent/prompts/{id}', token=token)

    def import_prompt(self, cid, token=None):
        """Pull a shared prompt into the agent library by CID."""
        return self._call('/agent/prompts/import', 'POST', {'cid': cid}, token=token)

    def compose(self, prompt, prompt_id=None, graph=None, token=None):
        """Describe a protocol in words; get back a validated graph."""
        body = {'prompt': prompt}
        if prompt_id:
            body['promptId'] = prompt_id
        if graph is not None:
            body['graph'] = self._graph(graph)
        return self._call('/agent/compose', 'POST', body, token=token)

    def agent(self):
        """Whether the agent module — the brain behind compose — is reachable."""
        return self._call('/agent/status')

    # --- the trading desk ---------------------------------------------------
    #
    # Every one of these is a proxy for a peer module's MCP tool. The token you
    # pass is that module's, not this one's — nothing here can sign.

    def venues(self, check=True):
        """Where this desk can trade, and whether each chain module is up."""
        return self._call(f'/dex/venues?check={"1" if check else "0"}')

    def dex_tokens(self, chain=None):
        """Tokens each venue knows by name."""
        return self._call('/dex/tokens' + (f'?chain={chain}' if chain else ''))

    def quote(self, chain, sell, buy, amount, slippage_bps=50, token=None):
        """What a trade would really get you, priced against live liquidity."""
        return self._call('/dex/quote', 'POST', {
            'chain': chain, 'sell': sell, 'buy': buy, 'amount': str(amount),
            'slippageBps': slippage_bps}, token=token)

    def swap(self, chain, sell, buy, amount, account=None, slippage_bps=50,
             confirm=False, dry_run=False, hotkey=None, password=None, token=None):
        """Trade, for real. The chain module signs and its own guards apply."""
        body = {'chain': chain, 'sell': sell, 'buy': buy, 'amount': str(amount),
                'slippageBps': slippage_bps, 'confirm': bool(confirm),
                'dryRun': bool(dry_run)}
        for key, value in (('account', account), ('hotkey', hotkey),
                           ('password', password)):
            if value:
                body[key] = value
        return self._call('/dex/swap', 'POST', body, token=token)

    def balances(self, chain, address=None, token=None):
        """What a wallet holds on one of these chains, read by its own module."""
        query = f'?chain={chain}' + (f'&address={address}' if address else '')
        return self._call('/dex/balances' + query, token=token)

    # --- the yields table ----------------------------------------------------
    #
    # DefiLlama's index, cached server-side and filtered here. Every rate is
    # theirs: this module normalises and ranks, it never invents a number.

    def yields(self, chain=None, project=None, symbol=None, q=None, min_tvl=None,
               stable=False, organic=False, sort='score', limit=40):
        """Live APR per pool. `organic` keeps only rates that are mostly fees."""
        query = {'chain': chain, 'project': project, 'symbol': symbol, 'q': q,
                 'min_tvl': min_tvl, 'sort': sort, 'limit': limit}
        if stable:
            query['stable'] = '1'
        if organic:
            query['organic'] = '1'
        return self._call('/yields' + _qs(query))

    def yield_protocols(self, chain=None, q=None, min_tvl=None, stable=False,
                        organic=False, sort='tvl', limit=40):
        """The APR for each DeFi protocol — TVL-weighted across its pools."""
        query = {'chain': chain, 'q': q, 'min_tvl': min_tvl, 'sort': sort, 'limit': limit}
        if stable:
            query['stable'] = '1'
        if organic:
            query['organic'] = '1'
        return self._call('/yields/protocols' + _qs(query))

    def yield_pool(self, id, history=True):
        """One pool, plus up to a year of how its rate has actually behaved."""
        return self._call(f'/yields/pool/{id}?history={"1" if history else "0"}')

    def yield_facets(self):
        """Which chains and projects the index knows right now."""
        return self._call('/yields/facets')

    # --- the treasury --------------------------------------------------------
    #
    # What you chose out of that table, locked, and paid out weekly on
    # BLOCTIME'S clock: Friday 12:00 EST, split pro-rata by BLOC. The schedule
    # and the split are arithmetic and are labelled projections; a locked
    # allocation is a transaction the `eth` module signed.

    def treasury(self):
        """The desk: allocations, the next four windows, and the contract."""
        return self._call('/treasury')

    def treasury_schedule(self, weeks=8):
        """The next N Friday windows and what each would release."""
        return self._call(f'/treasury/schedule?weeks={weeks}')

    def treasury_holders(self):
        """Who the payout splits across, and their live BLOC, from bloctime."""
        return self._call('/treasury/holders')

    def treasury_preview(self):
        """Next Friday in full: the pot, and what each holder would get."""
        return self._call('/treasury/preview')

    def treasury_onchain(self, token=None):
        """The bound treasury's live state, read off the chain."""
        return self._call('/treasury/onchain', token=token)

    def treasury_choose(self, amount, pool=None, project=None, chain=None,
                        symbol=None, apy=0, apy_base=0, tvl_usd=0, asset=None,
                        asset_address=None, term_weeks=4, return_principal=False,
                        note=None, id=None, token=None):
        """Record a choice. Writes the ledger only — nothing moves yet."""
        body = {'amount': str(amount), 'pool': pool, 'project': project,
                'chain': chain, 'symbol': symbol, 'apy': apy, 'apy_base': apy_base,
                'tvl_usd': tvl_usd, 'asset': asset, 'asset_address': asset_address,
                'term_weeks': term_weeks, 'return_principal': bool(return_principal),
                'note': note, 'id': id}
        return self._call('/treasury/allocations', 'POST',
                          {k: v for k, v in body.items() if v is not None}, token=token)

    def treasury_drop(self, id, token=None):
        """Delete a PLAN. A locked allocation cannot be deleted, by design."""
        return self._call(f'/treasury/allocations/{id}', 'DELETE', token=token)

    def treasury_watch(self, address, remove=False, token=None):
        """Add or remove an address from the local BLOC watch list."""
        return self._call('/treasury/participants', 'POST',
                          {'address': address, 'remove': bool(remove)}, token=token)

    def treasury_bind(self, address, network='base-sepolia', asset=None,
                      weight=None, decimals=18, token=None):
        """Point this node at a deployed ModBlocTimeTreasury."""
        body = {'address': address, 'network': network, 'decimals': decimals}
        for key, value in (('asset', asset), ('weight', weight)):
            if value:
                body[key] = value
        return self._call('/treasury/bind', 'POST', body, token=token)

    def treasury_lock(self, id, account, confirm=False, password=None, token=None):
        """Lock an allocation for real. THIS CANNOT BE RECALLED before the term."""
        body = {'id': id, 'account': account, 'confirm': bool(confirm)}
        if password:
            body['password'] = password
        return self._call('/treasury/lock', 'POST', body, token=token)

    def treasury_distribute(self, account, confirm=False, password=None, token=None):
        """Sweep this week's payout to the registered holders. Friday only."""
        body = {'account': account, 'confirm': bool(confirm)}
        if password:
            body['password'] = password
        return self._call('/treasury/distribute', 'POST', body, token=token)

    def treasury_claim(self, account, confirm=False, password=None, token=None):
        """Pull whatever the weekly splits have credited to this account."""
        body = {'account': account, 'confirm': bool(confirm)}
        if password:
            body['password'] = password
        return self._call('/treasury/claim', 'POST', body, token=token)

    def treasury_register(self, account, who=None, confirm=False, password=None,
                          token=None):
        """Put an address into the contract's registered set — on chain."""
        body = {'account': account, 'confirm': bool(confirm)}
        for key, value in (('who', who), ('password', password)):
            if value:
                body[key] = value
        return self._call('/treasury/register', 'POST', body, token=token)

    # --- mcp ----------------------------------------------------------------

    def mcp(self):
        """MCP transport notes and the tool list."""
        return self._call('/mcp')

    def mcp_tools(self):
        """Just the tool names this module exposes over MCP."""
        return [t['name'] for t in self._call('/mcp').get('tools', [])]

    # --- ops ----------------------------------------------------------------

    def build(self):
        """Compile the Rust API and the Next.js app."""
        return {'output': subprocess.run(
            [os.path.join(HERE, 'start.sh'), '--build-only'],
            capture_output=True, text=True, timeout=1800).stdout[-4000:]}

    def serve(self, background=True):
        """Start both processes (pm2 if available, plain background otherwise)."""
        script = os.path.join(HERE, 'start.sh')
        if background:
            subprocess.Popen([script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {'starting': True, 'api': API, 'app': f'http://localhost:{APP_PORT}/defi'}
        r = subprocess.run([script], capture_output=True, text=True, timeout=1800)
        return {'output': r.stdout[-4000:], 'error': r.stderr[-2000:]}

    def stop(self):
        """Stop both processes."""
        r = subprocess.run(['pm2', 'delete', 'defi-api', 'defi-app'],
                           capture_output=True, text=True)
        return {'output': (r.stdout or r.stderr)[-2000:]}

    def status(self):
        """Health of the API, the app, and the agent link."""
        return {
            'api': self._call('/health'),
            'compile': self._call('/compile/status'),
            'agent': self._call('/agent/status'),
            'app': self._app_status(),
        }

    def _app_status(self):
        try:
            with urllib.request.urlopen(f'http://localhost:{APP_PORT}/defi', timeout=4) as r:
                return {'up': r.status == 200, 'url': f'http://localhost:{APP_PORT}/defi'}
        except Exception as e:
            return {'up': False, 'error': str(e)}

    def test(self):
        """Run the Rust unit tests and check that every block still compiles."""
        api = os.path.join(HERE, 'src', 'api')
        rust = subprocess.run(['cargo', 'test', '--offline'], cwd=api,
                              capture_output=True, text=True, timeout=900)
        return {
            'rust': rust.stdout[-3000:] or rust.stderr[-3000:],
            'passed': rust.returncode == 0,
            'solc': self._call('/compile/status'),
        }

    def test_contract(self):
        """Run the BlocTime Treasury's Solidity tests against a real EVM.

        This module has no JS toolchain of its own, so it borrows bloctime's
        hardhat — the same one that tests BlocTime.sol. node_modules is COPIED,
        never symlinked: a symlink here has wiped a live module's deps before.
        """
        import shutil
        import tempfile
        bloctime = os.path.join(os.path.dirname(HERE), 'bloctime')
        deps = os.path.join(bloctime, 'node_modules')
        if not os.path.isdir(deps):
            return {'error': f'no hardhat to borrow at {deps} — install bloctime first'}
        blocks = os.path.join(HERE, 'src', 'api', 'blocks')
        work = tempfile.mkdtemp(prefix='defi-contract-test-')
        try:
            os.makedirs(os.path.join(work, 'contracts'))
            os.makedirs(os.path.join(work, 'test'))
            # symlinks=True, not the default: hardhat's .bin/hardhat is a
            # symlink whose relative require() only resolves from where it
            # points. Dereferencing it copies a file that cannot find itself.
            shutil.copytree(deps, os.path.join(work, 'node_modules'), symlinks=True)
            for name in ('common.sol', 'treasury.sol'):
                shutil.copy(os.path.join(blocks, name), os.path.join(work, 'contracts', name))
            shutil.copy(os.path.join(blocks, 'tests', 'Mocks.sol'),
                        os.path.join(work, 'contracts', 'Mocks.sol'))
            shutil.copy(os.path.join(blocks, 'tests', 'treasury.test.js'),
                        os.path.join(work, 'test', 'treasury.test.js'))
            with open(os.path.join(work, 'hardhat.config.js'), 'w') as fh:
                fh.write('require("@nomicfoundation/hardhat-toolbox");\n'
                         'module.exports = { solidity: { version: "0.8.24", '
                         'settings: { optimizer: { enabled: true, runs: 200 } } } };\n')
            run = subprocess.run([os.path.join(work, 'node_modules', '.bin', 'hardhat'), 'test'],
                                 cwd=work, capture_output=True, text=True, timeout=900)
            return {'passed': run.returncode == 0,
                    'output': (run.stdout or run.stderr)[-6000:]}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def readme(self):
        """Return the project README."""
        p = os.path.join(HERE, 'README.md')
        return m.get_text(p) if os.path.exists(p) else None
