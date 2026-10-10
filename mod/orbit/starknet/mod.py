import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.path.join(HERE, 'api')
sys.path.insert(0, API)
import abi  # noqa: E402
import chain  # noqa: E402
import strk20  # noqa: E402


class Mod:
    description = ("Starknet as one mod: an explorer app and a REST + MCP API "
                   "on one port, reading the chain over free public RPC with "
                   "failover (your own node first via STARKNET_RPC). Any "
                   "contract by its on-chain ABI, plus the STRK20 privacy "
                   "pool. Python stdlib only.")
    path = HERE
    config = json.load(open(os.path.join(HERE, 'config.json')))
    port = config['port']
    base = config['base_path']

    # ---- module protocol ----------------------------------------------

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        return {'name': 'starknet', 'description': self.description,
                'path': self.path, 'port': self.port,
                'networks': list(chain.NETWORKS),
                'endpoints': self.config['endpoints'],
                'fns': self.config['fns']}

    def readme(self):
        p = os.path.join(self.path, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ---- chain reads (thin passthroughs to chain.py) ------------------

    def status(self, network=None):
        return chain.status(network=network)

    def chain_id(self, network=None):
        return chain.chain_id(network=network)

    def block_number(self, network=None):
        return chain.block_number(network=network)

    def blocks(self, limit=10, network=None):
        return chain.blocks(limit, network=network)

    def search(self, q, network=None):
        return chain.lookup(q, network=network)

    def block(self, id='latest', full=False, network=None):
        return chain.block(id, full=full, network=network)

    def tx(self, hash, network=None):
        return chain.tx(hash, network=network)

    def receipt(self, hash, network=None):
        return chain.receipt(hash, network=network)

    def account(self, address, network=None):
        return chain.account(address, network=network)

    def balance(self, address, token='eth', network=None):
        return chain.balance(address, token, network=network)

    def nonce(self, address, network=None):
        return chain.nonce(address, network=network)

    def class_hash(self, address, network=None):
        return chain.class_hash(address, network=network)

    def storage(self, address, key, network=None):
        return chain.storage(address, key, network=network)

    def call(self, contract, entrypoint=None, calldata=None, selector=None,
             network=None):
        return chain.call(contract, entrypoint, calldata,
                          entry_selector=selector, network=network)

    def selector(self, name):
        return {'name': name, 'selector': chain.selector(name)}

    def rpc(self, method, params=None, network=None):
        return chain.rpc(method, params, network=network)

    # ---- any contract, by its on-chain ABI (abi.py) -------------------

    def contract(self, address, network=None):
        return abi.iface(address, network=network)

    def read(self, contract, function, args=None, network=None):
        return abi.read(contract, function, args, network=network)

    def encode(self, contract, function, args=None, network=None):
        return abi.encode(contract, function, args, network=network)

    def events(self, address, name=None, limit=20, network=None):
        return abi.events(address, name, limit=limit, network=network)

    # ---- STRK20 privacy pool (strk20.py) ------------------------------

    def strk20_pool(self, network=None):
        return strk20.state(network=network)

    def strk20_activity(self, event=None, limit=20, network=None):
        return strk20.activity(event, limit=limit, network=network)

    def strk20_user(self, address, network=None):
        return strk20.user(address, network=network)

    def strk20_note(self, note_id, network=None):
        return strk20.note(note_id, network=network)

    def strk20_nullifier(self, nullifier, network=None):
        return strk20.nullifier(nullifier, network=network)

    def strk20_helpers(self, network=None):
        return strk20.helpers(network=network)

    def strk20_helper(self, address, network=None):
        return strk20.helper(address, network=network)

    def strk20_invoke_action(self, helper, args=None, network=None):
        return strk20.invoke_action(helper, args, network=network)

    def strk20_docs(self, page=None, q=None):
        return strk20.docs(page, q)

    # ---- MCP (mcp.py) --------------------------------------------------

    def tools(self):
        import mcp
        return mcp.tool_list()

    def mcp(self, name, **args):
        """Run one MCP tool by name: Mod().mcp('strk20_pool')."""
        import mcp
        return mcp.call_tool(name, args)

    # ---- server -------------------------------------------------------

    def serve(self, port=None, background=False):
        """Run the REST API, MCP and the app on one port."""
        port = int(port or self.port)
        if not background:
            # loaded by path under its own name: a bare `import api` can hit
            # whatever `api` the caller already has in sys.modules
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'starknet_api', os.path.join(API, 'api.py'))
            server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(server)
            return server.serve(port)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(API, 'api.py'), '--port', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}/'}

    def build(self):
        """Build the app (app/build.sh) and publish it to app/dist."""
        out = subprocess.run(['bash', os.path.join(HERE, 'app', 'build.sh')],
                             capture_output=True, text=True)
        return {'ok': out.returncode == 0,
                'log': (out.stdout + out.stderr)[-4000:]}

    def kill(self, port=None):
        port = int(port or self.port)
        out = subprocess.run(['fuser', '-k', f'{port}/tcp'],
                             capture_output=True, text=True)
        return {'port': port, 'killed': out.returncode == 0}

    def test(self, offline=False):
        """Self-test: offline crypto vectors, then a live read unless offline."""
        result = {'offline': {**chain.selftest(), **abi.selftest()}}
        if not offline:
            s = chain.status()
            result['live'] = {'chain': s['chain'],
                              'block_number': s['block_number'],
                              'rpc': s['rpc']}
            result['api'] = json.load(urllib.request.urlopen(
                f'http://localhost:{self.port}/health', timeout=3)) \
                if self._up() else 'api not running (mod.py serve)'
        return result

    def _up(self):
        try:
            urllib.request.urlopen(f'http://localhost:{self.port}/health',
                                   timeout=2)
            return True
        except Exception:
            return False


if __name__ == '__main__':
    m = Mod()
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        m.serve()
    else:
        print(json.dumps(m.test(), indent=2))
