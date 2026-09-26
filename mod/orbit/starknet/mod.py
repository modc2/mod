import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chain  # noqa: E402


class Mod:
    description = ("Starknet as one mod: reads the chain over free public RPC "
                   "with failover (your own node first via STARKNET_RPC), "
                   "hashes entry-point selectors in-tree, serves a REST API "
                   "and a browser console on one port. Stdlib only.")
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

    # ---- server -------------------------------------------------------

    def serve(self, port=None, background=False):
        """Run the REST API and the console on one port."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'api.py'), '--port', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}/'}

    def kill(self, port=None):
        port = int(port or self.port)
        out = subprocess.run(['fuser', '-k', f'{port}/tcp'],
                             capture_output=True, text=True)
        return {'port': port, 'killed': out.returncode == 0}

    def test(self, offline=False):
        """Self-test: offline crypto vectors, then a live read unless offline."""
        result = {'offline': chain.selftest()}
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
