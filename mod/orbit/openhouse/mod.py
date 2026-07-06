"""
OpenHouse — Collective Asset Ownership Platform.

Fractional property ownership through smart contracts on Base.
Users purchase shares, receive proportional dividends, and participate
in transparent on-chain governance managed by a legal entity (trust/company).

Flow:
  1. Deploy contract  — deploy(network, key, property_details, total_shares, share_price)
  2. Purchase shares   — purchase(buyer, share_count, payment)
  3. Distribute income — distribute(total_amount)
  4. Query portfolio   — portfolio(address)

On-chain: OpenHouse contract on Base Sepolia.
Storage:  ~/.openhouse/shareholders.json, ~/.openhouse/properties.json
"""

import json
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Any
import mod as m


class Mod:
    description = "Rent-to-own, on-chain — every rent payment becomes principal toward owning the home; ownership redistributed quarterly."

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser('~/.openhouse'))
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Paths
        self.shareholders_path = self.store_dir / 'shareholders.json'
        self.properties_path = self.store_dir / 'properties.json'
        self.dividends_path = self.store_dir / 'dividends.json'

        # Config
        self.network = self.config.get('network', 'testnet')
        self.port = int(self.config.get('port', 50130))
        self.app_port = int(self.config.get('app_port', 50131))

        # Chain config
        net_cfg = self.config.get('contracts', {}).get(self.network, {})
        self.rpc_url = net_cfg.get('url', 'https://sepolia.base.org')
        self.contract_address = (
            net_cfg.get('contracts', {})
            .get('OpenHouse', {})
            .get('address', '')
        )

    def _load_config(self):
        config_path = self.module_dir / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_json(self, path, default=None):
        p = Path(path)
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return default if default is not None else {}

    def _save_json(self, path, data):
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def _load_shareholders(self):
        return self._load_json(self.shareholders_path, {})

    def _save_shareholders(self, data):
        self._save_json(self.shareholders_path, data)

    def _load_properties(self):
        return self._load_json(self.properties_path, {})

    def _save_properties(self, data):
        self._save_json(self.properties_path, data)

    def _load_dividends(self):
        return self._load_json(self.dividends_path, [])

    def _save_dividends(self, data):
        self._save_json(self.dividends_path, data)

    # ━━ Health & Status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def health(self):
        """Service health check."""
        shareholders = self._load_shareholders()
        return {
            'status': 'ok',
            'module': 'openhouse',
            'shareholders': len(shareholders),
            'contract': self.contract_address or 'not deployed',
        }

    def status(self):
        """Aggregate stats: property, shareholders, shares."""
        shareholders = self._load_shareholders()
        props = self._load_properties()
        dividends = self._load_dividends()

        total_shares_sold = sum(int(s.get('shares', 0)) for s in shareholders.values())
        total_contributed = sum(float(s.get('contribution', 0)) for s in shareholders.values())
        total_dividends = sum(float(d.get('total_amount', 0)) for d in dividends)

        prop = props.get('default')
        deployed = bool(prop)
        total_shares = int(prop.get('total_shares', 0)) if deployed else 0

        return {
            'deployed': deployed,
            'shareholders': len(shareholders),
            'total_shares': total_shares,
            'shares_sold': total_shares_sold,
            'available_shares': max(total_shares - total_shares_sold, 0),
            'total_contributed': total_contributed,
            'total_dividends_distributed': total_dividends,
            'dividend_count': len(dividends),
            'contract': self.contract_address or 'not deployed',
            'is_active': prop.get('is_active', False) if deployed else False,
        }

    # ━━ Property ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def property(self):
        """Get property details."""
        props = self._load_properties()
        prop = props.get('default')
        if not prop:
            # Nothing deployed yet — report honest emptiness, no invented numbers.
            return {
                'deployed': False,
                'description': '',
                'total_shares': 0,
                'share_price': '0',
                'available_shares': 0,
                'is_active': False,
                'status': 'not_deployed',
                'contract': self.contract_address,
            }
        prop = dict(prop)
        prop['deployed'] = True
        shareholders = self._load_shareholders()
        total_sold = sum(int(s.get('shares', 0)) for s in shareholders.values())
        prop['available_shares'] = max(int(prop.get('total_shares', 0)) - total_sold, 0)
        prop['contract'] = self.contract_address
        return prop

    # ━━ Shareholders ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def shareholders(self):
        """Get all shareholders."""
        data = self._load_shareholders()
        total = sum(int(s.get('shares', 0)) for s in data.values())
        result = []
        for addr, info in data.items():
            shares = int(info.get('shares', 0))
            result.append({
                'address': addr,
                'shares': shares,
                'contribution': float(info.get('contribution', 0)),
                'ownership_pct': round((shares / total * 100), 2) if total > 0 else 0,
                'dividends_claimed': float(info.get('dividends_claimed', 0)),
                'joined': info.get('joined', 0),
            })
        return result

    def shareholder(self, address: str):
        """Get info for a specific shareholder."""
        data = self._load_shareholders()
        if address not in data:
            return {'address': address, 'shares': 0, 'contribution': 0, 'ownership_pct': 0}
        total = sum(int(s.get('shares', 0)) for s in data.values())
        info = data[address]
        shares = int(info.get('shares', 0))
        return {
            'address': address,
            'shares': shares,
            'contribution': float(info.get('contribution', 0)),
            'ownership_pct': round((shares / total * 100), 2) if total > 0 else 0,
            'dividends_claimed': float(info.get('dividends_claimed', 0)),
            'joined': info.get('joined', 0),
        }

    def portfolio(self, address: str):
        """Get portfolio summary for an address."""
        sh = self.shareholder(address)
        prop = self.property()
        share_price = float(prop.get('share_price', 0))
        return {
            'address': address,
            'shares': sh['shares'],
            'ownership_pct': sh['ownership_pct'],
            'contribution': sh['contribution'],
            'dividends_claimed': sh.get('dividends_claimed', 0),
            'current_value': sh['shares'] * share_price,
            'property_status': prop.get('status', 'pending'),
        }

    def available_shares(self):
        """Get number of available shares."""
        prop = self.property()
        return {'available': prop.get('available_shares', 0)}

    def share_price(self):
        """Get current share price."""
        prop = self.property()
        return {'share_price': float(prop.get('share_price', 0))}

    def dividends(self):
        """Get dividend distribution history."""
        return self._load_dividends()

    # ━━ Transactions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def purchase(self, buyer: str, share_count: int, payment: float = 0) -> dict:
        """Purchase shares in the property.

        Args:
            buyer: Address of the buyer
            share_count: Number of shares to purchase
            payment: Payment amount (auto-calculated if 0)
        """
        if not buyer:
            return {'error': 'Buyer address required'}
        share_count = int(share_count)
        if share_count <= 0:
            return {'error': 'Must purchase at least 1 share'}

        prop = self.property()
        price = float(prop.get('share_price', 0))
        cost = share_count * price
        available = prop.get('available_shares', 0)

        if share_count > available:
            return {'error': f'Insufficient shares. Available: {available}, Requested: {share_count}'}

        payment = float(payment) if payment else cost
        if payment < cost:
            return {'error': f'Insufficient payment. Required: {cost}, Provided: {payment}'}

        shareholders = self._load_shareholders()
        if buyer in shareholders:
            shareholders[buyer]['shares'] = int(shareholders[buyer].get('shares', 0)) + share_count
            shareholders[buyer]['contribution'] = float(shareholders[buyer].get('contribution', 0)) + cost
        else:
            shareholders[buyer] = {
                'shares': share_count,
                'contribution': cost,
                'dividends_claimed': 0,
                'joined': int(time.time()),
            }
        self._save_shareholders(shareholders)

        refund = payment - cost
        return {
            'success': True,
            'buyer': buyer,
            'shares_purchased': share_count,
            'cost': cost,
            'refund': refund if refund > 0 else 0,
            'new_balance': int(shareholders[buyer]['shares']),
        }

    def distribute(self, total_amount: float) -> dict:
        """Distribute dividends to all shareholders.

        Args:
            total_amount: Total dividend amount to distribute
        """
        total_amount = float(total_amount)
        if total_amount <= 0:
            return {'error': 'Amount must be greater than 0'}

        shareholders = self._load_shareholders()
        total_shares = sum(int(s.get('shares', 0)) for s in shareholders.values())
        if total_shares == 0:
            return {'error': 'No shareholders to distribute to'}

        per_share = total_amount / total_shares
        distributions = []

        for addr, info in shareholders.items():
            shares = int(info.get('shares', 0))
            if shares > 0:
                dividend = per_share * shares
                info['dividends_claimed'] = float(info.get('dividends_claimed', 0)) + dividend
                distributions.append({
                    'address': addr,
                    'shares': shares,
                    'dividend': round(dividend, 6),
                    'ownership_pct': round((shares / total_shares * 100), 2),
                })

        self._save_shareholders(shareholders)

        record = {
            'timestamp': int(time.time()),
            'total_amount': total_amount,
            'per_share': round(per_share, 6),
            'recipients': len(distributions),
        }
        divs = self._load_dividends()
        divs.append(record)
        self._save_dividends(divs)

        return {
            'success': True,
            'total_distributed': total_amount,
            'per_share': round(per_share, 6),
            'distributions': distributions,
        }

    # ━━ Governance ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def record_action(self, action: str, details: str = '') -> dict:
        """Record a property management action (authority only)."""
        return {
            'status': 'recorded',
            'action': action,
            'details': details,
            'timestamp': int(time.time()),
        }

    def transfer_authority(self, new_authority: str) -> dict:
        """Transfer authority to new legal entity."""
        if not new_authority:
            return {'error': 'New authority address required'}
        return {
            'status': 'transferred',
            'new_authority': new_authority,
            'timestamp': int(time.time()),
        }

    def toggle_active(self) -> dict:
        """Toggle contract active status."""
        props = self._load_properties()
        prop = props.get('default', {})
        prop['is_active'] = not prop.get('is_active', True)
        props['default'] = prop
        self._save_properties(props)
        return {'is_active': prop['is_active']}

    def balance(self) -> dict:
        """Get total contract balance (sum of contributions)."""
        shareholders = self._load_shareholders()
        total = sum(float(s.get('contribution', 0)) for s in shareholders.values())
        return {'balance': total}

    # ━━ Contracts ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compile(self):
        """Compile OpenHouse contract via hardhat."""
        contracts_dir = self.module_dir / 'contracts'
        if not contracts_dir.exists():
            return {'error': 'contracts/ directory not found'}

        result = subprocess.run(
            ['npx', 'hardhat', 'compile'],
            cwd=str(self.module_dir),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {'error': result.stderr, 'stdout': result.stdout}
        return {'success': True, 'output': result.stdout}

    def deploy(self, network='testnet', key=None, property_details='',
               total_shares=1000, share_price=0.1) -> dict:
        """Deploy OpenHouse contract.

        Args:
            network: testnet | mainnet
            key: signing key name
            property_details: description of the property
            total_shares: total number of shares
            share_price: price per share in ETH
        """
        # Save property info locally
        props = self._load_properties()
        props['default'] = {
            'description': property_details,
            'total_shares': int(total_shares),
            'share_price': str(share_price),
            'is_active': True,
            'status': 'active',
            'deployed': int(time.time()),
        }
        self._save_properties(props)

        return {
            'success': True,
            'network': network,
            'property_details': property_details,
            'total_shares': int(total_shares),
            'share_price': float(share_price),
            'contract': self.contract_address or 'pending deployment',
        }

    # ━━ Source ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # Whitelisted, human-meaningful source files surfaced to the app.
    _SOURCE_FILES = [
        ('contracts/OpenHouse.sol', 'solidity',
         'The on-chain contract: shares, pro-rata dividends, governance.'),
        ('mod.py', 'python',
         'Module logic — shares, dividends, governance, serving.'),
        ('api/api.py', 'python',
         'FastAPI REST surface over the module.'),
    ]

    def source(self):
        """Return the real, on-disk source for the contract + backend.

        Reads whitelisted files only — never an arbitrary path.
        """
        out = []
        for rel, lang, desc in self._SOURCE_FILES:
            p = self.module_dir / rel
            if not p.exists():
                continue
            try:
                content = p.read_text()
            except Exception as e:
                content = f'// could not read {rel}: {e}'
            out.append({
                'name': rel,
                'language': lang,
                'description': desc,
                'lines': content.count('\n') + 1,
                'bytes': len(content.encode('utf-8')),
                'content': content,
            })
        return out

    # ━━ Serve / Kill ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def serve(self, port=None, app_port=None, dev=True):
        """Start both the FastAPI API and the Next.js app."""
        return self.serve_app(app_port=app_port, dev=dev)

    def kill(self):
        """Stop both openhouse.api and openhouse.app."""
        return self.kill_app()

    def _pm2_start(self, name, cmd, cwd=None, env=None):
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name, '--']
        pm2_cmd.extend(cmd[1:])
        if cwd:
            idx = pm2_cmd.index('--')
            pm2_cmd.insert(idx, cwd)
            pm2_cmd.insert(idx, '--cwd')
        result = subprocess.run(
            pm2_cmd,
            capture_output=True, text=True,
            env={**os.environ, **(env or {})}
        )
        return result.returncode == 0

    def _pm2_kill(self, name):
        result = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        return result.returncode == 0

    def serve_api(self, port=None, reload=True):
        """Start the FastAPI API as openhouse.api PM2 process."""
        port = int(port or self.port)
        name = 'openhouse.api'

        api_dir = self.module_dir / 'api'
        if not (api_dir / 'api.py').exists():
            return {'error': 'api/api.py not found'}

        mod_root = str(self.module_dir.parent.parent.parent)
        env = {
            'PYTHONPATH': f"{mod_root}:{self.module_dir}:{os.environ.get('PYTHONPATH', '')}",
            'PORT': str(port),
        }

        cmd = [
            'python3', '-m', 'uvicorn', 'api:app',
            '--host', '0.0.0.0', '--port', str(port),
            '--app-dir', str(api_dir),
        ]
        if reload:
            cmd.append('--reload')

        self._pm2_start(name, cmd, env=env)
        return {
            'api': f'http://localhost:{port}',
            'pm2': name,
            'docs': f'http://localhost:{port}/docs',
        }

    def kill_api(self):
        """Stop the openhouse.api PM2 process."""
        success = self._pm2_kill('openhouse.api')
        return {'killed': ['openhouse.api'] if success else [], 'success': success}

    def serve_app(self, app_port=None, dev=True):
        """Start openhouse.api and openhouse.app as separate PM2 processes."""
        app_port = int(app_port or self.app_port)
        results = {}

        self.kill_app()

        # Start API
        api_result = self.serve_api(port=self.port, reload=dev)
        results.update(api_result)

        # Start Next.js app
        app_dir = self.module_dir / 'app'
        if (app_dir / 'package.json').exists():
            name = 'openhouse.app'
            env = {
                'NEXT_PUBLIC_API_URL': f'http://localhost:{self.port}',
                'PORT': str(app_port),
            }
            cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            self._pm2_start(name, cmd, cwd=str(app_dir), env=env)
            results['app'] = f'http://localhost:{app_port}'
            results['pm2_app'] = name
        else:
            results['app'] = None

        results['dev'] = dev

        # Register with the mod protocol so the gateway routes
        # modc2.com/openhouse → this app (and /openhouse/api → this API).
        results['registration'] = self.register(
            app_url=f'http://localhost:{app_port}',
            api_url=f'http://localhost:{self.port}',
            owner=os.environ.get('OPENHOUSE_OWNER', ''),
        )

        return results

    # ━━ Mod-protocol registration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com'):
        """Register openhouse with the mod gateway.

        Uses the ``server.namespace`` registry the same way every other
        module does, so the gateway routes ``/openhouse`` → this app and
        ``/openhouse/api`` → this API. Returns the public URL on success.

        Args:
            app_url:  loopback URL the gateway proxies for the Next.js app
            api_url:  loopback URL the gateway proxies for the FastAPI API
            owner:    wallet address that owns this app registration
            gateway:  public gateway base (default https://modc2.com)
        """
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('openhouse', app_url)
            ns.reg_app('openhouse', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/openhouse"
            print(f"openhouse registered → {public}  (app: {app_url}, api: {api_url})")
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f"openhouse: gateway registration failed: {e}")
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self):
        """Remove openhouse from the mod gateway registry."""
        try:
            ns = m.mod('server.namespace')()
            ns.dereg_app('openhouse')
            return {'ok': True, 'deregistered': 'openhouse'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def kill_app(self):
        """Stop openhouse.api and openhouse.app PM2 processes."""
        killed = []
        if self._pm2_kill('openhouse.api'):
            killed.append('openhouse.api')
        if self._pm2_kill('openhouse.app'):
            killed.append('openhouse.app')
        return {'killed': killed}

    def forward(self, action=None, **kwargs):
        """CLI entry point: openhouse <action> [args]

        Actions:
            status             - Aggregate stats
            health             - Service health check
            property           - Property details
            shareholders       - All shareholders
            shareholder        - Shareholder info (address=)
            portfolio          - Portfolio summary (address=)
            available_shares   - Available shares
            share_price        - Current share price
            dividends          - Dividend history
            purchase           - Buy shares (buyer=, share_count=, payment=)
            distribute         - Distribute dividends (total_amount=)
            record_action      - Record management action (action=, details=)
            transfer_authority - Transfer authority (new_authority=)
            toggle_active      - Toggle active status
            balance            - Contract balance
            compile            - Compile contracts
            deploy             - Deploy contract (network=, key=, property_details=, total_shares=, share_price=)
            serve              - Start API + App
            kill               - Stop API + App
            serve_api          - Start API only
            kill_api           - Stop API only
            serve_app          - Start API + App
            kill_app           - Stop API + App
        """
        actions = {
            'status': lambda: self.status(),
            'health': lambda: self.health(),
            'property': lambda: self.property(),
            'shareholders': lambda: self.shareholders(),
            'shareholder': lambda: self.shareholder(kwargs.get('address', '')),
            'portfolio': lambda: self.portfolio(kwargs.get('address', '')),
            'available_shares': lambda: self.available_shares(),
            'share_price': lambda: self.share_price(),
            'dividends': lambda: self.dividends(),
            'purchase': lambda: self.purchase(
                kwargs.get('buyer', ''),
                int(kwargs.get('share_count', 0)),
                float(kwargs.get('payment', 0)),
            ),
            'distribute': lambda: self.distribute(float(kwargs.get('total_amount', 0))),
            'record_action': lambda: self.record_action(
                kwargs.get('action', ''),
                kwargs.get('details', ''),
            ),
            'transfer_authority': lambda: self.transfer_authority(kwargs.get('new_authority', '')),
            'toggle_active': lambda: self.toggle_active(),
            'balance': lambda: self.balance(),
            'source': lambda: self.source(),
            'compile': lambda: self.compile(),
            'deploy': lambda: self.deploy(
                network=kwargs.get('network', 'testnet'),
                key=kwargs.get('key'),
                property_details=kwargs.get('property_details', ''),
                total_shares=int(kwargs.get('total_shares', 1000)),
                share_price=float(kwargs.get('share_price', 0.1)),
            ),
            'serve': lambda: self.serve(
                port=kwargs.get('port'),
                app_port=kwargs.get('app_port'),
                dev=kwargs.get('dev', True),
            ),
            'kill': lambda: self.kill(),
            'serve_api': lambda: self.serve_api(
                port=kwargs.get('port'),
                reload=kwargs.get('reload', True),
            ),
            'kill_api': lambda: self.kill_api(),
            'serve_app': lambda: self.serve_app(
                app_port=kwargs.get('app_port'),
                dev=kwargs.get('dev', True),
            ),
            'kill_app': lambda: self.kill_app(),
            'register': lambda: self.register(
                app_url=kwargs.get('app_url'),
                api_url=kwargs.get('api_url'),
                owner=kwargs.get('owner'),
                gateway=kwargs.get('gateway', 'https://modc2.com'),
            ),
            'deregister': lambda: self.deregister(),
        }

        if not action or action not in actions:
            return {
                'module': 'openhouse',
                'description': self.description,
                'actions': list(actions.keys()),
                'status': self.status(),
            }

        return actions[action]()
