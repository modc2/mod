"""
Bridge — Substrate/Solana to EVM identity bridge.

Snapshot-based token claims with cryptographic identity commitments.
Users sign with SubWallet (sr25519) or Phantom (ed25519) to link
their source address to an EVM address on Base.

Flow:
  1. Check snapshot  — in_snapshot(address) -> balance
  2. Commit identity — sign "commit {evmAddr}" -> commit()
  3. Claim tokens    — claim(address) (requires verified commitment)

On-chain: BridgeableToken (ERC20 + Ownable) on Base Sepolia.
Storage:  ~/.bridge/claims.json, ~/.bridge/commitments.json
Served via core server (m.serve('bridge')) — no custom API needed.
"""

import json
import os
import time
import subprocess
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from web3 import Web3
import mod as m

logger = logging.getLogger(__name__)


class Mod:
    description = "Substrate/Solana to EVM identity bridge with snapshot claims and on-chain commitments."

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser('~/.bridge'))
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Paths
        self.claims_path = self.store_dir / 'claims.json'
        self.commitments_path = self.store_dir / 'commitments.json'
        self.used_sigs_path = self.store_dir / 'used_signatures.json'
        self.snapshot_dir = self.module_dir / 'snapshot'

        # Config
        self.owner_address = self.config.get('owner', '')
        self.network = self.config.get('network', 'testnet')
        self.signer_key = os.environ.get('BRIDGE_SIGNER_KEY', '')
        self.port = int(self.config.get('port', 8840))
        self.app_port = int(self.config.get('app_port', 8841))

        # Chain config from contracts.{network}
        net_cfg = self.config.get('contracts', {}).get(self.network, {})
        self.rpc_url = net_cfg.get('url', 'https://sepolia.base.org')
        self.contract_address = (
            net_cfg.get('contracts', {})
            .get('BridgeableToken', {})
            .get('address', self.config.get('contract_address', ''))
        )

        # LocalFS for ABI storage
        self.lfs = m.mod('store.localfs')()
        self._abi_cid = self.config.get('abi_cid', '')

        # Snapshot data (loaded once, normalize from raw units)
        raw = self._load_snapshot('total_balances.json')
        self._total_balances = {addr: float(val) / 1e9 for addr, val in raw.items()}

        if not os.environ.get('BRIDGE_ADMIN_KEY'):
            logger.warning('BRIDGE_ADMIN_KEY not set - admin operations disabled')

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

    def _load_snapshot(self, filename):
        return self._load_json(self.snapshot_dir / filename, {})

    def _load_claims(self):
        return self._load_json(self.claims_path, {})

    def _save_claims(self, claims):
        self._save_json(self.claims_path, claims)

    def _load_commitments(self):
        return self._load_json(self.commitments_path, {})

    def _save_commitments(self, commitments):
        self._save_json(self.commitments_path, commitments)

    def _load_used_sigs(self) -> set:
        data = self._load_json(self.used_sigs_path, [])
        return set(data) if isinstance(data, list) else set()

    def _save_used_sig(self, sig_hash: str):
        used = self._load_used_sigs()
        used.add(sig_hash)
        self._save_json(self.used_sigs_path, list(used))

    # ━━ Health & Status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def health(self):
        """Service health check."""
        return {
            'status': 'ok',
            'module': 'bridge',
            'snapshot_addresses': len(self._total_balances),
            'claims': len(self._load_claims()),
            'admin_key_configured': bool(os.environ.get('BRIDGE_ADMIN_KEY')),
        }

    def status(self):
        """Aggregate stats: totals, claims, unclaimed."""
        claims = self._load_claims()
        total_owed = sum(float(v) for v in self._total_balances.values())
        total_claimed = sum(float(c.get('amount', 0)) for c in claims.values())
        return {
            'total_addresses': len(self._total_balances),
            'total_owed': total_owed,
            'total_claimed': total_claimed,
            'total_unclaimed': total_owed - total_claimed,
            'claim_count': len(claims),
        }

    def owner(self):
        """Return the owner address."""
        return self.owner_address

    # ━━ Contract Info ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def contract_info(self) -> dict:
        """Return contract details including ABI CID from localfs."""
        net_cfg = self.config.get('contracts', {}).get(self.network, {})
        result = {
            'network': self.network,
            'chain_id': net_cfg.get('chainId', ''),
            'rpc_url': self.rpc_url,
            'contract_address': self.contract_address,
            'abi_cid': self._abi_cid,
        }
        if self._abi_cid and self.lfs.valid_cid(self._abi_cid):
            result['abi_stored'] = True
        else:
            result['abi_stored'] = False
        return result

    def store_abi(self) -> dict:
        """Compile and store the contract ABI in localfs, saving the CID."""
        abi_path = self._find_abi_path()
        if not abi_path:
            return {'error': 'ABI not found — run compile first'}

        with open(abi_path) as f:
            artifact = json.load(f)

        abi = artifact.get('abi', [])
        cid = self.lfs.put(abi, pin=True)
        self._abi_cid = cid

        config_path = self.module_dir / 'config.json'
        config = self._load_json(config_path, {})
        config['abi_cid'] = cid
        self._save_json(config_path, config)

        return {'success': True, 'abi_cid': cid, 'functions': len(abi)}

    def _get_abi(self) -> list:
        """Load ABI from localfs CID, falling back to artifact file."""
        if self._abi_cid and self.lfs.valid_cid(self._abi_cid):
            return self.lfs.get(self._abi_cid)
        abi_path = self._find_abi_path()
        if abi_path:
            with open(abi_path) as f:
                return json.load(f).get('abi', [])
        return []

    def _find_abi_path(self) -> Path:
        """Find the compiled ABI artifact."""
        candidates = [
            self._chain_bridge_dir() / 'artifacts' / 'contracts' / 'Bridge.sol' / 'BridgeableToken.json',
            self._chain_dir() / 'artifacts' / 'contracts' / 'bridge' / 'Bridge.sol' / 'BridgeableToken.json',
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    # ━━ Snapshot ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def in_snapshot(self, address: str) -> dict:
        """Check if an address is in the snapshot and return its balance."""
        found = address in self._total_balances
        balance = float(self._total_balances.get(address, 0))
        return {'address': address, 'in_snapshot': found, 'balance': balance}

    def get_total_balances(self) -> dict:
        """Return the full snapshot balance map."""
        return self._total_balances

    # ━━ Claims ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def claim(self, address: str, signature: str = None, recipient: str = None, **kwargs) -> dict:
        """Process a token claim.

        Requires a verified commitment for the source address AND proof of ownership.
        The caller must sign 'claim {timestamp}' with the source address to prove ownership.

        Args:
            address: source address (must have a verified commitment)
            signature: hex signature of 'claim {timestamp}' proving ownership
            recipient: EVM address — must match committed evm_address
        """
        if not address:
            return {'error': 'Source address required'}
        if not signature:
            return {'error': 'Signature required to prove ownership of source address'}

        # Require a verified commitment (proves ownership via signature)
        commitments = self._load_commitments()
        commitment = commitments.get(address)
        if not commitment:
            return {'error': f'No verified commitment for {address}. Commit first.'}

        source_type = commitment.get('source_type')
        timestamp = kwargs.get('timestamp', int(time.time()))

        try:
            valid = self._verify_claim_signature(address, signature, timestamp, source_type)
        except Exception as e:
            return {'error': f'Signature verification failed: {str(e)}'}
        if not valid:
            return {'error': 'Invalid signature — caller does not own source address'}

        committed_evm = commitment.get('evm_address', '')
        if recipient and recipient.lower() != committed_evm.lower():
            return {'error': 'Recipient does not match committed EVM address'}
        recipient = committed_evm

        total = float(self._total_balances.get(address, 0))
        if total <= 0:
            return {'error': f'No allocation for {address}'}

        claims = self._load_claims()
        if address in claims:
            return {'error': f'Already claimed for {address}'}

        amount = self.unclaimed(address)
        if amount <= 0:
            return {'error': 'Nothing to claim'}

        claims[address] = {
            'amount': amount,
            'recipient': recipient,
            'from': address,
            'timestamp': time.time(),
        }
        self._save_claims(claims)

        return {
            'success': True,
            'amount': amount,
            'recipient': recipient,
            'from': address,
        }

    def has_claimed(self, address: str) -> dict:
        """Check if an address has claimed."""
        claims = self._load_claims()
        return {'claimed': address in claims, 'address': address}

    def unclaimed(self, address: str) -> float:
        """Return unclaimed balance for an address."""
        total = float(self._total_balances.get(address, 0))
        claimed = float(self._load_claims().get(address, {}).get('amount', 0))
        return total - claimed

    def claims_array(self) -> list:
        """Return all claims as a list."""
        return [
            {'address': addr, **data}
            for addr, data in self._load_claims().items()
        ]

    def get_claims(self) -> dict:
        """Return all claims as a dict."""
        return self._load_claims()

    def delete_claim(self, address: str, auth_token: str = None) -> dict:
        """Delete a claim (owner only, verified server-side)."""
        caller = self._verify_owner_token(auth_token)
        if not caller:
            return {'error': 'Valid owner auth_token required'}
        claims = self._load_claims()
        if address not in claims:
            return {'error': f'No claim found for {address}'}
        del claims[address]
        self._save_claims(claims)
        return {'success': True, 'deleted': address}

    def reset(self, auth_token: str = None) -> dict:
        """Reset all bridge data (claims, commitments, used signatures).

        Requires admin auth via BRIDGE_ADMIN_KEY. For testing only.
        """
        caller = self._verify_owner_token(auth_token)
        if not caller:
            return {'error': 'Valid admin auth_token required'}

        self._save_json(self.claims_path, {})
        self._save_json(self.commitments_path, {})
        self._save_json(self.used_sigs_path, [])

        return {'success': True, 'reset': ['claims', 'commitments', 'used_signatures']}

    def _verify_owner_token(self, token: str) -> Optional[str]:
        """Verify admin access via BRIDGE_ADMIN_KEY env var.

        Uses a server-side secret rather than client-provided identity.
        Set BRIDGE_ADMIN_KEY in your environment for admin operations.
        """
        if not token:
            return None
        admin_key = os.environ.get('BRIDGE_ADMIN_KEY', '')
        # Constant-time compare — protects against timing oracle on the admin key.
        import hmac
        if admin_key and hmac.compare_digest(token, admin_key):
            return self.owner_address
        return None

    # ━━ Commitments ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def commit(self, source_address: str, evm_address: str, signature: str, source_type: str) -> dict:
        """Sign and commit a source address to an EVM address.

        The user signs "commit {evm_address}" with their source wallet.
        Signature is verified, then the commitment is stored locally
        and posted on-chain to the BridgeableToken contract.

        Args:
            source_address: sr25519 or solana public address
            evm_address: target EVM address (0x...)
            signature: hex-encoded signature of "commit {evm_address}"
            source_type: 'substrate' or 'solana'
        """
        if not source_address:
            return {'error': 'Source address required'}
        if not evm_address:
            return {'error': 'EVM address required'}
        if not signature:
            return {'error': 'Signature required'}
        if source_type not in ('substrate', 'solana'):
            return {'error': 'source_type must be substrate or solana'}
        if source_address not in self._total_balances:
            return {'error': f'Address not in snapshot: {source_address}'}

        commitments = self._load_commitments()
        if source_address in commitments:
            return {'error': f'Already committed: {source_address}'}

        try:
            valid = self._verify_signature(source_address, signature, evm_address, source_type)
        except Exception as e:
            return {'error': f'Signature verification failed: {str(e)}'}
        if not valid:
            return {'error': 'Invalid signature'}

        chain_result = self._post_commitment_onchain(source_address, evm_address, source_type)

        commitments[source_address] = {
            'source_address': source_address,
            'evm_address': evm_address,
            'source_type': source_type,
            'timestamp': time.time(),
            'chain': chain_result,
        }
        self._save_commitments(commitments)

        return {
            'success': True,
            'source_address': source_address,
            'evm_address': evm_address,
            'source_type': source_type,
            'chain': chain_result,
        }

    def update_commitment(self, source_address: str, evm_address: str, signature: str, source_type: str) -> dict:
        """Update an existing commitment to a new EVM address.

        Requires a fresh signature proving continued ownership.
        The caller must sign "commit {new_evm_address}" with their source wallet.
        """
        if not source_address or not evm_address or not signature:
            return {'error': 'source_address, evm_address, and signature are required'}
        if source_type not in ('substrate', 'solana'):
            return {'error': 'source_type must be substrate or solana'}

        commitments = self._load_commitments()
        existing = commitments.get(source_address)
        if not existing:
            return {'error': f'No existing commitment for {source_address}'}

        if existing.get('source_type') != source_type:
            return {'error': 'source_type does not match original commitment'}

        if evm_address.lower() == existing.get('evm_address', '').lower():
            return {'error': 'New EVM address is the same as current'}

        # Check the address hasn't already claimed (can't change after claim)
        claims = self._load_claims()
        if source_address in claims:
            return {'error': 'Cannot update commitment after claim'}

        try:
            valid = self._verify_signature(source_address, signature, evm_address, source_type)
        except Exception as e:
            return {'error': f'Signature verification failed: {str(e)}'}
        if not valid:
            return {'error': 'Invalid signature'}

        previous_evm = existing.get('evm_address', '')
        commitments[source_address] = {
            'source_address': source_address,
            'evm_address': evm_address,
            'source_type': source_type,
            'timestamp': time.time(),
            'previous_evm': previous_evm,
        }
        self._save_commitments(commitments)

        return {
            'success': True,
            'source_address': source_address,
            'evm_address': evm_address,
            'source_type': source_type,
            'previous_evm': previous_evm,
        }

    def get_commitments(self) -> dict:
        """Return all commitments."""
        return self._load_commitments()

    def get_commitment(self, source_address: str) -> dict:
        """Return commitment for a specific source address."""
        commitments = self._load_commitments()
        if source_address in commitments:
            return commitments[source_address]
        return {'error': f'No commitment for {source_address}'}

    # ━━ Signature Verification ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _verify_signature(self, source_address, signature, evm_address, source_type):
        """Verify "commit {evm_address}" was signed by source_address.

        Includes replay protection — each commitment can only be used once.
        """
        import hashlib
        commit_data = f'{source_address}:{evm_address}:{source_type}'.encode('utf-8')
        commit_hash = hashlib.sha256(commit_data).hexdigest()
        if commit_hash in self._load_used_sigs():
            raise ValueError('Commitment already used (replay rejected)')

        message = f'commit {evm_address}'.encode('utf-8')
        sig_bytes = bytes.fromhex(signature.replace('0x', ''))

        valid = False
        if source_type == 'substrate':
            key = m.mod('key.sr25519')
            pub = key.resolve_public_key(address=source_address)
            valid = key.verify_data(sig_bytes, message, pub)
        elif source_type == 'solana':
            key = m.mod('key.solana')
            pub = key.resolve_public_key(address=source_address)
            valid = key.verify_data(sig_bytes, message, pub)

        if valid:
            self._save_used_sig(commit_hash)
        return valid

    def _verify_claim_signature(self, source_address, signature, timestamp, source_type):
        """Verify "claim {timestamp}" was signed by source_address.

        Includes replay protection — each claim signature can only be used once.
        Timestamp must be within 5 minutes to prevent old signatures being reused.
        """
        import hashlib
        now = int(time.time())
        if abs(now - timestamp) > 300:
            raise ValueError('Timestamp too old or too far in future (must be within 5 minutes)')

        claim_data = f'{source_address}:claim:{timestamp}'.encode('utf-8')
        claim_hash = hashlib.sha256(claim_data).hexdigest()
        if claim_hash in self._load_used_sigs():
            raise ValueError('Claim signature already used (replay rejected)')

        message = f'claim {timestamp}'.encode('utf-8')
        sig_bytes = bytes.fromhex(signature.replace('0x', ''))

        valid = False
        if source_type == 'substrate':
            key = m.mod('key.sr25519')
            pub = key.resolve_public_key(address=source_address)
            valid = key.verify_data(sig_bytes, message, pub)
        elif source_type == 'solana':
            key = m.mod('key.solana')
            pub = key.resolve_public_key(address=source_address)
            valid = key.verify_data(sig_bytes, message, pub)

        if valid:
            self._save_used_sig(claim_hash)
        return valid

    # ━━ Serve / Kill ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def serve(self, dev=False, api_url=None, app_url=None, **kwargs):
        """Start the bridge API and Next.js app via PM2.

        Args:
            dev:     run Next.js in dev mode (default False — production build + start)
            api_url: public API URL baked into the prod build
                     (default https://modc2.com/api/bridge)
            app_url: public app URL baked into the prod build
                     (default https://modc2.com/bridge)

        Logs: pm2 logs bridge.api / pm2 logs bridge.app
        """
        log_dir = Path('/tmp/bridge')
        log_dir.mkdir(parents=True, exist_ok=True)

        self.kill()
        self._wait_ports_free([self.port, self.app_port])

        api_dir = self.module_dir / 'api'
        app_dir = self.module_dir / 'app'

        api_url = api_url or 'https://modc2.com/api/bridge'
        app_url = app_url or 'https://modc2.com/bridge'

        # ── API (Rust / axum via pm2) ──────────────────────
        binary = api_dir / 'target' / 'release' / 'bridge-api'
        print(f'[bridge] Building Rust API ({api_dir})...')
        build = subprocess.run(
            ['cargo', 'build', '--release'],
            cwd=str(api_dir), capture_output=True, text=True,
        )
        if build.returncode != 0:
            print(f'[bridge] cargo build failed:\n{build.stdout}\n{build.stderr}')
            return {'success': False, 'error': 'cargo build failed', 'stderr': build.stderr}
        if not binary.exists():
            return {'success': False, 'error': f'binary missing after build: {binary}'}

        api_env = {
            **os.environ,
            'PORT': str(self.port),
            'BRIDGE_DATA_DIR': str(self.store_dir),
            'BRIDGE_SNAPSHOT_DIR': str(self.snapshot_dir),
            'BRIDGE_ENV': 'production' if not dev else 'development',
        }
        subprocess.run([
            'pm2', 'start', str(binary), '--name', 'bridge.api',
            '--output', str(log_dir / 'api.log'),
            '--error', str(log_dir / 'api.log'),
        ], cwd=str(api_dir), env=api_env, capture_output=True)
        print(f'[bridge] API started on :{self.port} (pm2: bridge.api, Rust)')

        # ── App (Next.js via pm2) ──────────────────────────
        result = {'api_port': self.port}
        if app_dir.exists() and (app_dir / 'package.json').exists():
            if dev:
                app_env = {
                    **os.environ,
                    'NEXT_PUBLIC_API_URL': f'http://localhost:{self.port}',
                }
                subprocess.run([
                    'pm2', 'start', 'npx', '--name', 'bridge.app',
                    '--output', str(log_dir / 'app.log'),
                    '--error', str(log_dir / 'app.log'),
                    '--', 'next', 'dev', '-p', str(self.app_port),
                ], cwd=str(app_dir), env=app_env, capture_output=True)
                print(f'[bridge] App started on :{self.app_port} dev (pm2: bridge.app)')
            else:
                build_env = {
                    **os.environ,
                    'NODE_ENV': 'production',
                    'NEXT_PUBLIC_API_URL': api_url,
                    'NEXT_PUBLIC_APP_URL': app_url,
                    'API_INTERNAL_URL': f'http://localhost:{self.port}',
                }
                print(f'[bridge] Building Next.js app (NEXT_PUBLIC_API_URL={api_url})...')
                build = subprocess.run(
                    ['npx', 'next', 'build'],
                    cwd=str(app_dir), env=build_env,
                    capture_output=True, text=True,
                )
                if build.returncode != 0:
                    print(f'[bridge] Build failed:\n{build.stdout}\n{build.stderr}')
                    return {'success': False, 'error': 'next build failed', 'stderr': build.stderr}

                subprocess.run([
                    'pm2', 'start', 'npx', '--name', 'bridge.app',
                    '--output', str(log_dir / 'app.log'),
                    '--error', str(log_dir / 'app.log'),
                    '--', 'next', 'start', '-p', str(self.app_port),
                ], cwd=str(app_dir), env=build_env, capture_output=True)
                print(f'[bridge] App started on :{self.app_port} prod → {app_url} (pm2: bridge.app)')
            result['app_port'] = self.app_port
            result['api_url'] = api_url
            result['app_url'] = app_url

        return {'success': True, **result}

    def _wait_ports_free(self, ports, timeout=5):
        """Wait until all ports are free."""
        import socket
        deadline = time.time() + timeout
        while time.time() < deadline:
            all_free = True
            for port in ports:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    if s.connect_ex(('127.0.0.1', port)) == 0:
                        all_free = False
                        break
            if all_free:
                return
            time.sleep(0.3)

    def _kill_port(self, port):
        """Kill all processes listening on a port."""
        killed = []
        try:
            result = subprocess.run(
                ['lsof', '-ti', f':{port}'],
                capture_output=True, text=True,
            )
            pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
            for pid in pids:
                try:
                    os.kill(pid, 9)
                    killed.append(pid)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass
        return killed

    def kill(self, **kwargs):
        """Stop the bridge API and app via PM2."""
        killed = []

        for name in ('bridge.api', 'bridge.app', 'bridge'):
            result = subprocess.run(
                ['pm2', 'delete', name],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                killed.append(name)

        # Clean up any orphans still on our ports
        for port in (self.port, self.app_port):
            for pid in self._kill_port(port):
                killed.append(f'port {port} (pid {pid})')

        if killed:
            print(f'[bridge] Killed: {", ".join(killed)}')
        return {'success': True, 'killed': killed}

    # ━━ On-Chain ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _post_commitment_onchain(self, source_address, evm_address, source_type):
        """Post commitment to BridgeableToken.commit() on Base."""
        if not self.contract_address or not self.signer_key:
            return {'error': 'Contract address or signer key not configured'}

        try:
            w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            account = w3.eth.account.from_key(m.key(self.signer_key).private_key)

            abi = self._get_abi()
            if not abi:
                return {'error': 'Contract ABI not found — run compile or store_abi first'}

            contract = w3.eth.contract(
                address=Web3.to_checksum_address(self.contract_address),
                abi=abi,
            )

            source_hash = Web3.keccak(text=source_address)
            evm_addr = Web3.to_checksum_address(evm_address)

            tx = contract.functions.commit(
                source_hash, evm_addr, source_address, source_type
            ).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address, 'pending'),
                'gasPrice': w3.eth.gas_price,
                'chainId': w3.eth.chain_id,
            })

            signed = w3.eth.account.sign_transaction(tx, account.key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

            return {
                'tx_hash': receipt.transactionHash.hex(),
                'block': receipt.blockNumber,
                'status': 'confirmed' if receipt.status == 1 else 'failed',
            }
        except Exception as e:
            return {'error': f'On-chain commit failed: {str(e)}'}

    # ━━ Contracts ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _chain_bridge_dir(self):
        return Path(__file__).parent.parent.parent.parent / 'core' / 'chain' / 'chain' / 'mods' / 'bridge'

    def _chain_dir(self):
        return Path(__file__).parent.parent.parent.parent / 'core' / 'chain'

    def _ensure_hardhat(self):
        """Symlink node_modules and hardhat config from chain root."""
        bridge_dir = self._chain_bridge_dir().resolve()
        chain_dir = self._chain_dir().resolve()
        mod_root = Path(__file__).parent.parent.parent.parent.resolve()

        bridge_real = Path(os.path.realpath(bridge_dir))
        chain_real = Path(os.path.realpath(chain_dir))
        mod_root_real = Path(os.path.realpath(mod_root))

        # Validate paths are within the mod tree
        if os.path.commonpath([mod_root_real, bridge_real]) != str(mod_root_real):
            raise ValueError(f'bridge_dir outside mod tree: {bridge_dir}')
        if os.path.commonpath([mod_root_real, chain_real]) != str(mod_root_real):
            raise ValueError(f'chain_dir outside mod tree: {chain_dir}')

        local_nm = bridge_dir / 'node_modules'
        chain_nm = chain_dir / 'node_modules'
        if not local_nm.exists() and chain_nm.exists():
            os.symlink(str(chain_nm), str(local_nm))

        shared_cfg = bridge_dir.parent / 'hardhat.config.js'
        local_cfg = bridge_dir / 'hardhat.config.js'
        if not local_cfg.exists() and shared_cfg.exists():
            import shutil
            shutil.copy2(str(shared_cfg), str(local_cfg))

    def compile(self):
        """Compile BridgeableToken via hardhat."""
        self._ensure_hardhat()
        result = subprocess.run(
            ['npx', 'hardhat', 'compile'],
            cwd=str(self._chain_bridge_dir()),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {'error': result.stderr, 'stdout': result.stdout}
        return {'success': True, 'output': result.stdout}

    def test(self):
        """Run BridgeableToken hardhat tests."""
        self._ensure_hardhat()
        test_dir = self._chain_bridge_dir() / 'test'
        if not test_dir.exists():
            return {'error': 'No test/ directory found'}

        result = subprocess.run(
            ['npx', 'hardhat', 'test'],
            cwd=str(self._chain_bridge_dir()),
            capture_output=True, text=True,
        )
        return {
            'success': result.returncode == 0,
            'exit_code': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
        }

    def deploy(self, network='testnet', key=None, name='Bridge Token',
               symbol='BRG', initial_supply=0):
        """Deploy BridgeableToken to Base.

        Args:
            network: testnet (Base Sepolia) | mainnet | ganache
            key: signing key name from mod key store
            name: ERC20 token name
            symbol: ERC20 token symbol
            initial_supply: tokens minted to deployer (0 for bridge)
        """
        compile_result = self.compile()
        if 'error' in compile_result:
            return compile_result

        try:
            bridge_mod = m.mod('chain.bridge')(network=network, key=key or 'test')
            address = bridge_mod.deploy(
                network=network, key=key,
                name=name, symbol=symbol,
                initial_supply=initial_supply,
            )

            self.contract_address = address
            config_path = self.module_dir / 'config.json'
            config = self._load_json(config_path, {})
            config['contract_address'] = address
            config['network'] = network
            self._save_json(config_path, config)

            return {
                'success': True,
                'address': address,
                'network': network,
                'name': name,
                'symbol': symbol,
            }
        except Exception as e:
            return {'error': f'Deploy failed: {str(e)}'}
