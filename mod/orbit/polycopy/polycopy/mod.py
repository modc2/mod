"""
polycopy - multi-chain copy trading engine

Rust-powered core (polycopy_rs) with PyO3 bindings for monitoring
swap events, scoring traders, and executing copy trades across
Base, Polygon, Arbitrum, and Ethereum.

Usage:
    import mod as m
    pc = m.mod('polycopy')()
    pc.forward('status')
    pc.forward('start')
    pc.forward('watch', address='0x...')
    pc.forward('top_traders', top=10, min_trades_per_day=5)
"""
import asyncio
import json
import os
import subprocess

import mod as c

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(MOD_DIR)

DEFAULT_CHAINS = [
    {
        'chain_id': 8453, 'name': 'base', 'enabled': True,
        'rpc_urls': [
            'https://mainnet.base.org', 'https://base.llamarpc.com',
            'https://base-rpc.publicnode.com', 'https://base.drpc.org',
            'https://rpc.ankr.com/base',
        ],
        'routers': [
            {'address': '0x2626664c2603336E57B271c5C0b26F421741e481', 'name': 'Uniswap V3', 'dex_type': 'UniswapV3'},
            {'address': '0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891', 'name': 'SushiSwap', 'dex_type': 'UniswapV2'},
        ],
        'proxy_address': None,
    },
    {
        'chain_id': 137, 'name': 'polygon', 'enabled': True,
        'rpc_urls': [
            'https://polygon-rpc.com', 'https://polygon.llamarpc.com',
            'https://polygon-bor-rpc.publicnode.com', 'https://polygon.drpc.org',
            'https://rpc.ankr.com/polygon',
        ],
        'routers': [
            {'address': '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45', 'name': 'Uniswap V3', 'dex_type': 'UniswapV3'},
            {'address': '0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506', 'name': 'SushiSwap', 'dex_type': 'UniswapV2'},
            {'address': '0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff', 'name': 'QuickSwap', 'dex_type': 'UniswapV2'},
        ],
        'proxy_address': None,
    },
    {
        'chain_id': 42161, 'name': 'arbitrum', 'enabled': True,
        'rpc_urls': [
            'https://arb1.arbitrum.io/rpc', 'https://arbitrum.llamarpc.com',
            'https://arbitrum-one-rpc.publicnode.com', 'https://arbitrum.drpc.org',
            'https://rpc.ankr.com/arbitrum',
        ],
        'routers': [
            {'address': '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45', 'name': 'Uniswap V3', 'dex_type': 'UniswapV3'},
            {'address': '0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506', 'name': 'SushiSwap', 'dex_type': 'UniswapV2'},
            {'address': '0xc873fEcbd354f5A56E00E710B90EF4201db2448d', 'name': 'Camelot', 'dex_type': 'UniswapV2'},
        ],
        'proxy_address': None,
    },
    {
        'chain_id': 1, 'name': 'ethereum', 'enabled': False,
        'rpc_urls': [
            'https://eth.llamarpc.com', 'https://ethereum-rpc.publicnode.com',
            'https://eth.drpc.org', 'https://rpc.ankr.com/eth',
            'https://rpc.flashbots.net',
        ],
        'routers': [
            {'address': '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45', 'name': 'Uniswap V3', 'dex_type': 'UniswapV3'},
            {'address': '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F', 'name': 'SushiSwap', 'dex_type': 'UniswapV2'},
        ],
        'proxy_address': None,
    },
]

DEFAULT_CONFIG = {
    'chains': DEFAULT_CHAINS,
    'wallets': [],
    'max_trade_usd': 100,
    'slippage_bps': 50,
    'position_pct': 10.0,
    'daily_limit_usd': 1000,
    'auto_discover': False,
    'min_score': 70.0,
    'poll_interval_ms': 4000,
}


class Mod:
    description = """
    Multi-chain copy trading engine.
    Monitors profitable wallets across Base, Polygon, Arbitrum, Ethereum.
    Executes copy trades through on-chain CopyTradeProxy contracts.
    Rust-powered core with PyO3 bindings for speed.
    """

    name = 'polycopy'
    api_port = 50130
    app_port = 3130

    def __init__(self, config_path=None, **kwargs):
        self.dir = ROOT_DIR
        self._dir = ROOT_DIR
        self._mod_dir = MOD_DIR
        self._app_dir = os.path.join(ROOT_DIR, 'app')
        self.config_path = config_path or os.path.join(ROOT_DIR, 'config.json')
        self.config = self._load_config()
        self.config.update({k: v for k, v in kwargs.items() if v is not None})
        self.engine = None

    def _load_config(self):
        import copy
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        if os.path.exists(self.config_path):
            with open(self.config_path) as f:
                saved = json.load(f)
            cfg.update(saved)
        return cfg

    def _save_config(self):
        save = {k: v for k, v in self.config.items() if k != 'private_key'}
        with open(self.config_path, 'w') as f:
            json.dump(save, f, indent=2)

    def _ensure_engine(self):
        if self.engine is None:
            try:
                import polycopy_rs
                self.engine = polycopy_rs.PolycopyEngine(json.dumps(self.config))
            except ImportError:
                raise RuntimeError(
                    "polycopy_rs not built. Run:\n"
                    f"  cd {os.path.join(MOD_DIR, 'polycopy-rs')}\n"
                    "  maturin develop --release"
                )

    # ── dispatch ────────────────────────────────────────────────────

    def forward(self, cmd='status', **kwargs):
        commands = {
            'start': self.start,
            'stop': self.stop,
            'status': self.status,
            'watch': self.watch,
            'unwatch': self.unwatch,
            'scores': self.scores,
            'trades': self.trades,
            'pause': self.pause,
            'unpause': self.unpause,
            'config': self.show_config,
            'set': self.set_config,
            'rpc': self.rpc_stats,
            'wallets': self.list_wallets,
            'build': self.build,
            'scrape': self.scrape,
            'top_traders': self.top_traders,
            'serve': self.serve,
            'kill': self.kill,
        }
        fn = commands.get(cmd, self.status)
        return fn(**kwargs)

    # ── engine ops ──────────────────────────────────────────────────

    def start(self, **kwargs):
        self._ensure_engine()
        self.engine.start()
        self._save_config()
        chains = [c['name'] for c in self.config.get('chains', []) if c.get('enabled')]
        mode = 'copy' if self.config.get('private_key') else 'monitor-only'
        return {'status': 'running', 'chains': chains, 'mode': mode}

    def stop(self, **kwargs):
        if self.engine:
            self.engine.stop()
        return {'status': 'stopped'}

    def status(self, **kwargs):
        running = False
        if self.engine:
            try:
                running = self.engine.is_running()
            except Exception:
                pass
        mode = 'copy' if self.config.get('private_key') else 'monitor-only'
        return {
            'running': running,
            'mode': mode,
            'chains': [
                {'name': c['name'], 'chain_id': c['chain_id'], 'enabled': c.get('enabled')}
                for c in self.config.get('chains', [])
            ],
            'wallets': len(self.config.get('wallets', [])),
        }

    def watch(self, address=None, label=None, **kwargs):
        if not address:
            return {'error': 'address required'}
        wallets = self.config.setdefault('wallets', [])
        if address not in wallets:
            wallets.append(address)
            self._save_config()
        if self.engine:
            self.engine.add_wallet(address, label)
        return {'watched': address, 'total': len(wallets)}

    def unwatch(self, address=None, **kwargs):
        if not address:
            return {'error': 'address required'}
        wallets = self.config.get('wallets', [])
        if address in wallets:
            wallets.remove(address)
            self._save_config()
        if self.engine:
            self.engine.remove_wallet(address)
        return {'unwatched': address, 'total': len(wallets)}

    def list_wallets(self, **kwargs):
        if self.engine:
            return json.loads(self.engine.get_wallets())
        return self.config.get('wallets', [])

    def scores(self, **kwargs):
        self._ensure_engine()
        return json.loads(self.engine.get_scores())

    def trades(self, limit=20, **kwargs):
        self._ensure_engine()
        return json.loads(self.engine.get_trades(int(limit)))

    def pause(self, chain_id=None, chain=None, **kwargs):
        cid = chain_id or self._chain_id(chain)
        if not cid:
            return {'error': 'chain_id or chain name required'}
        self._ensure_engine()
        tx = self.engine.pause(int(cid))
        return {'paused': cid, 'tx': tx}

    def unpause(self, chain_id=None, chain=None, **kwargs):
        cid = chain_id or self._chain_id(chain)
        if not cid:
            return {'error': 'chain_id or chain name required'}
        self._ensure_engine()
        tx = self.engine.unpause(int(cid))
        return {'unpaused': cid, 'tx': tx}

    def rpc_stats(self, **kwargs):
        self._ensure_engine()
        return json.loads(self.engine.get_rpc_stats())

    # ── config ──────────────────────────────────────────────────────

    def show_config(self, **kwargs):
        return {k: v for k, v in self.config.items() if k != 'private_key'}

    def set_config(self, key=None, value=None, **kwargs):
        if not key:
            return {'error': 'key required'}
        FLOAT_KEYS = ('max_trade_usd', 'daily_limit_usd', 'position_pct', 'min_score')
        INT_KEYS = ('slippage_bps', 'poll_interval_ms')
        BOOL_KEYS = ('auto_discover',)
        if key in FLOAT_KEYS:
            value = float(value)
        elif key in INT_KEYS:
            value = int(value)
        elif key in BOOL_KEYS:
            value = str(value).lower() in ('true', '1', 'yes')
        self.config[key] = value
        self._save_config()
        return {'set': key, 'value': value}

    # ── build ───────────────────────────────────────────────────────

    def build(self, **kwargs):
        rs_dir = os.path.join(MOD_DIR, 'polycopy-rs')
        if not os.path.exists(rs_dir):
            return {'error': f'Rust crate not found at {rs_dir}'}
        ret = os.system(f'cd {rs_dir} && maturin develop --release')
        return {'status': 'built' if ret == 0 else 'failed', 'exit_code': ret}

    # ── scraper / top traders ────────────────────────────────────────

    def scrape(self, min_trades_per_day=10, lookback_days=7, chains=None, **kwargs):
        """Run the historical swap scraper across chains."""
        from api.scraper import Scraper
        chain_ids = chains or [c['chain_id'] for c in self.config.get('chains', []) if c.get('enabled')]
        if isinstance(chain_ids, str):
            chain_ids = json.loads(chain_ids)
        s = Scraper(
            min_trades_per_day=int(min_trades_per_day),
            lookback_days=int(lookback_days),
            chains=chain_ids,
        )
        asyncio.get_event_loop().run_until_complete(s.run()) if asyncio.get_event_loop().is_running() is False else asyncio.run(s.run())
        return {
            'progress': s.get_progress(),
            'traders': s.get_traders(min_trades_per_day=int(min_trades_per_day)),
        }

    def top_traders(self, top=10, min_trades_per_day=5, lookback_days=7,
                    chains=None, auto_watch=False, **kwargs):
        """Scan for top N traders by performance over the lookback period.

        Runs the scraper, ranks traders by avg trades/day, and optionally
        adds them to the watch list.

        Args:
            top: number of top traders to return (default 10)
            min_trades_per_day: minimum avg trades/day threshold (default 5)
            lookback_days: how many days back to scan (default 7)
            chains: list of chain IDs to scan (default: enabled chains)
            auto_watch: if True, automatically add top traders to watch list
        """
        from api.scraper import Scraper
        chain_ids = chains or [c['chain_id'] for c in self.config.get('chains', []) if c.get('enabled')]
        if isinstance(chain_ids, str):
            chain_ids = json.loads(chain_ids)
        top = int(top)
        min_trades_per_day = int(min_trades_per_day)

        s = Scraper(
            min_trades_per_day=min_trades_per_day,
            lookback_days=int(lookback_days),
            chains=chain_ids,
        )
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(asyncio.run, s.run()).result()
            else:
                loop.run_until_complete(s.run())
        except RuntimeError:
            asyncio.run(s.run())

        all_traders = s.get_traders(min_trades_per_day=min_trades_per_day)
        selected = all_traders[:top]

        if auto_watch or str(kwargs.get('auto_watch', '')).lower() in ('true', '1', 'yes'):
            for t in selected:
                self.watch(address=t['address'], label=f"top-{selected.index(t)+1}")

        return {
            'top': top,
            'min_trades_per_day': min_trades_per_day,
            'lookback_days': int(lookback_days),
            'chains': chain_ids,
            'total_scanned': len(all_traders),
            'selected': len(selected),
            'traders': selected,
            'auto_watched': bool(auto_watch),
        }

    def set_key(self, private_key=None, **kwargs):
        if not private_key:
            return {'error': 'private_key required'}
        self.config['private_key'] = private_key
        return {'set': True}

    def set_proxy(self, chain=None, chain_id=None, address=None, **kwargs):
        if not address:
            return {'error': 'address required'}
        cid = chain_id or self._chain_name_to_id(chain)
        if not cid:
            return {'error': 'chain or chain_id required'}
        for c in self.config.get('chains', []):
            if c['chain_id'] == cid or c['name'] == (chain or '').lower():
                c['proxy_address'] = address
                self._save_config()
                return {'chain': c['name'], 'proxy': address}
        return {'error': f'chain not found: {chain or chain_id}'}

    # ── helpers ─────────────────────────────────────────────────────

    def _chain_name_to_id(self, name):
        if not name:
            return None
        for c in self.config.get('chains', []):
            if c['name'] == name.lower():
                return c['chain_id']
        return None

    def _chain_id(self, name):
        if not name:
            return None
        for c in self.config.get('chains', []):
            if c['name'] == name.lower():
                return c['chain_id']
        return None

    # ── serve / kill (PM2) ─────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True, api_only=False, app_only=False, **kwargs):
        """Start the polycopy API server and/or Next.js app via PM2.

        Args:
            api_port:  API server port (default 50130)
            app_port:  Next.js app port (default 3130)
            dev:       run in dev mode (default True)
            api_only:  only start the API server
            app_only:  only start the Next.js app
        """
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port
        results = {}

        if not app_only:
            results['api'] = self._serve_api(api_port, dev=dev)

        if not api_only:
            results['app'] = self._serve_app(app_port, dev=dev)

        return results

    def _serve_api(self, port=None, dev=True):
        port = port or self.api_port
        cwd = self._mod_dir

        if dev:
            cmd = f'uvicorn api.api:app --host 0.0.0.0 --port {port} --reload'
        else:
            cmd = f'uvicorn api.api:app --host 0.0.0.0 --port {port}'

        script = os.path.join(cwd, '_serve.sh')
        with open(script, 'w') as f:
            f.write(f'#!/bin/bash\ncd {cwd}\n{cmd}\n')
        os.chmod(script, 0o755)

        try:
            pm2 = c.mod('pm.pm2')()
            name = 'polycopy-api'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=cwd, interpreter='bash')
            return {'status': 'running', 'port': port, 'manager': 'pm2', 'name': name}
        except Exception:
            proc = subprocess.Popen(['bash', script], cwd=cwd)
            return {'status': 'running', 'port': port, 'manager': 'subprocess', 'pid': proc.pid}

    def _serve_app(self, port=None, dev=True):
        port = port or self.app_port
        cwd = self._app_dir

        if not os.path.exists(os.path.join(cwd, 'node_modules')):
            subprocess.run(['npm', 'install'], cwd=cwd, capture_output=True)

        cmd = f'npm run {"dev" if dev else "start"} -- -p {port}'

        script = os.path.join(cwd, '_serve.sh')
        with open(script, 'w') as f:
            f.write(f'#!/bin/bash\ncd {cwd}\nexport NEXT_PUBLIC_API_URL=http://localhost:{self.api_port}\n{cmd}\n')
        os.chmod(script, 0o755)

        try:
            pm2 = c.mod('pm.pm2')()
            name = 'polycopy-app'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=cwd, interpreter='bash')
            return {'status': 'running', 'port': port, 'manager': 'pm2', 'name': name}
        except Exception:
            proc = subprocess.Popen(['bash', script], cwd=cwd)
            return {'status': 'running', 'port': port, 'manager': 'subprocess', 'pid': proc.pid}

    def kill(self, service=None, **kwargs):
        """Stop running services.

        Args:
            service: 'api', 'app', or None (both)
        """
        results = {}
        try:
            pm2 = c.mod('pm.pm2')()
            if service in (None, 'api') and pm2.exists('polycopy-api'):
                pm2.kill('polycopy-api')
                results['api'] = 'killed'
            if service in (None, 'app') and pm2.exists('polycopy-app'):
                pm2.kill('polycopy-app')
                results['app'] = 'killed'
        except Exception as e:
            results['error'] = str(e)
        return results

    def service_status(self, **kwargs):
        """Check if PM2 services are running."""
        results = {'api_port': self.api_port, 'app_port': self.app_port}
        try:
            pm2 = c.mod('pm.pm2')()
            results['api'] = 'running' if pm2.exists('polycopy-api') else 'stopped'
            results['app'] = 'running' if pm2.exists('polycopy-app') else 'stopped'
        except Exception:
            results['api'] = 'unknown'
            results['app'] = 'unknown'
        return results

    def __repr__(self):
        running = False
        if self.engine:
            try:
                running = self.engine.is_running()
            except Exception:
                pass
        chains = [c['name'] for c in self.config.get('chains', []) if c.get('enabled')]
        return f"<Polycopy chains={chains} wallets={len(self.config.get('wallets', []))} running={running}>"
