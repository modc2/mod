"""
Monero: explorer, wallet, view-key scanner, spending and swaps.

  explorer   info / block / tx / mempool / price / supply / network / search
  keys       seed phrases, addresses, subaddresses, integrated addresses
  wallet     encrypted local wallets, full or view-only
  scan       find your own outputs with a view key, locally
  send       build, preview and relay real transactions via monero-wallet-rpc
  bridge     XMR <-> 630 assets through a custodial swap provider
  mcp        mcp.py -- the same work as tools, on stdio or POST /mcp

Two things are true of Monero that shape this whole module:

  * there is no such thing as looking up an address balance. Finding your own
    money means scanning the chain with your view key, which `wallet_scan`
    does locally, in Python, without ever sending that key anywhere;
  * building a spend needs CLSAG and Bulletproofs+, which this module does not
    reimplement. `send` drives monero-wallet-rpc instead and is a dry run
    unless you pass broadcast=True.

`capabilities()` states exactly which parts work without any external help,
which need a node, and which need a wallet RPC.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from . import bridge as _bridge
    from . import crypto as _crypto
    from . import daemon as _daemon
    from . import mnemonic as _mnemonic
    from . import scan as _scan
    from . import wallet as _wallet
    from . import walletrpc as _walletrpc
except ImportError:  # loaded as a loose module by the mod runtime
    import bridge as _bridge
    import crypto as _crypto
    import daemon as _daemon
    import mnemonic as _mnemonic
    import scan as _scan
    import wallet as _wallet
    import walletrpc as _walletrpc

ATOMIC = _daemon.ATOMIC


def _err(e) -> dict:
    return {"error": str(e), "error_type": type(e).__name__}


def _pids_on_port(port: int) -> list:
    """Which processes are listening on a port.

    `lsof -ti :PORT` is the usual idiom and returns nothing at all on this
    host -- a next-server bound to :50691 stays invisible to it, so a kill
    based on lsof alone silently does nothing and the next start dies with
    EADDRINUSE. `ss` and `fuser` both see it, so try all three.
    """
    pids = set()
    try:
        out = subprocess.run(['ss', '-ltnpH', f'sport = :{port}'],
                             capture_output=True, text=True, timeout=5).stdout
        pids.update(int(m) for m in re.findall(r'pid=(\d+)', out))
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    if not pids:
        for cmd in (['fuser', '-n', 'tcp', str(port)], ['lsof', '-ti', f':{port}']):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=5).stdout
                pids.update(int(p) for p in out.split())
                if pids:
                    break
            except (subprocess.SubprocessError, OSError, ValueError):
                continue
    return sorted(pids)


class Mod:
    description = ("Monero explorer, encrypted wallet, local view-key scanner, "
                   "spending via monero-wallet-rpc, cross-chain swaps, and 45 "
                   "MCP tools for agents")

    fns = [
        # explorer
        'info', 'block', 'tx', 'mempool', 'price', 'supply', 'network', 'search',
        'fee', 'ring',
        # keys and addresses
        'validate', 'seed_new', 'keys_from_seed', 'subaddress', 'integrated',
        # local wallets
        'wallet_create', 'wallet_restore', 'wallet_watch', 'wallet_list',
        'wallet_info', 'wallet_new_address', 'wallet_integrated', 'wallet_label',
        'wallet_reveal', 'wallet_delete', 'wallet_restore_height', 'wallet_scan',
        # spending (monero-wallet-rpc)
        'rpc_status', 'balance', 'transfers', 'send', 'send_confirm', 'sweep',
        'broadcast_raw', 'rpc_open', 'rpc_load_wallet', 'key_images',
        # swaps
        'bridge_routes', 'bridge_assets', 'bridge_quote', 'bridge_start',
        'bridge_status',
        # agents
        'mcp', 'mcp_tools', 'mcp_call',
        # meta
        'capabilities', 'status', 'token', 'serve', 'app', 'kill', 'test',
    ]

    # The local REST server the web app talks to (see api.py).
    rest_port = int(os.environ.get('MONERO_REST_PORT', 8940))

    def __init__(self, config=None, **kwargs):
        self._dir = Path(__file__).parent.parent
        self._log_dir = Path('/tmp/monero')
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._config = config or self._load_config()
        self._load_env()
        self.api_port = int(self._config.get('port', 50690))
        self.app_port = int(self._config.get('app_port', 50691))
        self._daemon = None
        self._rpc = None

    # ── Plumbing ───────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        cfg = self._dir / 'config.json'
        if cfg.exists():
            with open(cfg) as f:
                return json.load(f)
        return {}

    def _load_env(self):
        env_path = self._dir / '.env'
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

    @property
    def daemon(self):
        if self._daemon is None:
            self._daemon = _daemon.Daemon()
        return self._daemon

    @property
    def rpc(self):
        if self._rpc is None:
            self._rpc = _walletrpc.WalletRPC()
        return self._rpc

    def forward(self, **kwargs):
        return self.info()

    # ── Explorer ───────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Chain overview: height, difficulty, mempool, price."""
        try:
            out = self.daemon.info()
        except _daemon.DaemonError as e:
            return _err(e)
        try:
            out.update(self.daemon.price())
        except _daemon.DaemonError:
            pass
        return out

    def block(self, height: int = None, hash: str = None) -> dict:
        """Block by height or hash; the latest one when neither is given."""
        try:
            return self.daemon.block(height=height, hash=hash)
        except _daemon.DaemonError as e:
            return _err(e)

    def tx(self, txid: str) -> dict:
        """Transaction details.

        Amounts and recipients are encrypted on chain, so what is public is the
        shape of the transaction: ring size, input and output counts, fee.
        """
        try:
            out = self.daemon.transaction(txid)
            out['visible'] = ("Monero hides amounts and recipients. What you see "
                              "here is structure, not who paid whom.")
            return out
        except _daemon.DaemonError as e:
            return _err(e)

    def mempool(self, limit: int = 25) -> dict:
        """Transactions waiting to be mined."""
        try:
            return self.daemon.mempool(limit)
        except _daemon.DaemonError as e:
            return _err(e)

    def price(self) -> dict:
        """XMR price and market data."""
        try:
            return self.daemon.price()
        except _daemon.DaemonError as e:
            return _err(e)

    def supply(self) -> dict:
        """Circulating supply and tail emission."""
        try:
            return self.daemon.supply()
        except _daemon.DaemonError as e:
            return _err(e)

    def network(self) -> dict:
        """Consensus and node health."""
        try:
            info = self.daemon.info()
        except _daemon.DaemonError as e:
            return _err(e)
        out = {k: info.get(k) for k in
               ('height', 'difficulty', 'hashrate', 'network', 'hard_fork_version',
                'tx_count', 'tx_pool_size', 'block_size_limit', 'top_block_hash')}
        out['node'] = self.daemon.node_info()
        try:
            out['fee'] = self.daemon.fee_estimate()
        except _daemon.DaemonError:
            pass
        return out

    def fee(self, priority: int = 1, size_bytes: int = 1500) -> dict:
        """What a transaction of this size would cost right now."""
        try:
            f = self.daemon.fee_estimate(priority)
        except _daemon.DaemonError as e:
            return _err(e)
        total = (f['fee_per_byte'] or 0) * int(size_bytes)
        out = dict(f, size_bytes=int(size_bytes), fee=total,
                   fee_xmr=_daemon.xmr(total))
        try:
            price = self.daemon.price().get('price_usd')
            if price:
                out['fee_usd'] = round(_daemon.xmr(total) * float(price), 4)
        except (_daemon.DaemonError, TypeError, ValueError):
            pass
        out['priorities'] = {0: 'slow', 1: 'normal', 2: 'high', 3: 'priority'}
        return out

    def ring(self, index: int, count: int = 1) -> dict:
        """Show the ring members at a global output index.

        The point of a ring is that every member is a plausible spender, which
        is easier to believe once you have looked at one.
        """
        try:
            outs = self.daemon.outputs(list(range(int(index), int(index) + max(1, count))))
        except _daemon.DaemonError as e:
            return _err(e)
        return {'outputs': outs, 'count': len(outs),
                'note': "Each entry is a real output on the chain. A spend names "
                        "16 of these and proves it owns one, without saying which."}

    def search(self, query: str) -> dict:
        """Find a block, transaction or address."""
        q = (query or '').strip()
        if not q:
            return {'error': 'empty query'}
        if q.isdigit():
            return {'type': 'block', 'result': self.block(height=int(q))}
        if len(q) == 64 and all(c in '0123456789abcdefABCDEF' for c in q):
            result = self.tx(txid=q)
            if 'error' not in result:
                return {'type': 'transaction', 'result': result}
            return {'type': 'block', 'result': self.block(hash=q)}
        if len(q) in (95, 106):
            parsed = self.validate(q)
            if parsed.get('valid'):
                parsed['note'] = (
                    "Monero addresses have no public balance or history -- "
                    "nothing on chain links an address to a transaction. To see "
                    "what this address received you need its view key: "
                    "wallet_watch then wallet_scan.")
            return {'type': 'address', 'result': parsed}
        return {'error': f'unrecognised query: {q[:40]}'}

    # ── Keys and addresses ─────────────────────────────────────────────────

    def validate(self, address: str) -> dict:
        """Check an address and say what kind it is."""
        try:
            parsed = _crypto.decode_address(address)
        except _crypto.CryptoError as e:
            # "invalid" is the answer, not a failure to answer -- so `reason`
            # rather than `error`, which callers read as a fault.
            return {'address': address, 'valid': False, 'reason': str(e)}
        return dict(parsed, valid=True)

    def seed_new(self) -> dict:
        """A fresh 25-word seed phrase and the wallet it produces.

        Nothing is stored. Use wallet_create for a wallet the module keeps.
        """
        phrase = _mnemonic.generate()
        keys = _crypto.keys_from_seed(_mnemonic.decode(phrase))
        return {'seed_phrase': phrase, 'address': keys['address'],
                'view_secret_key': keys['view_secret_key'],
                'warning': 'This was generated in memory and not saved anywhere. '
                           'Anyone who sees the phrase can spend the funds.'}

    def keys_from_seed(self, seed_phrase: str = None, seed_hex: str = None,
                       network: str = 'mainnet') -> dict:
        """Derive the four keys and the address from a seed phrase or hex seed."""
        try:
            if seed_phrase:
                seed = _mnemonic.decode(seed_phrase)
            elif seed_hex:
                seed = bytes.fromhex(seed_hex.strip())
            else:
                return {'error': 'give seed_phrase (25 words) or seed_hex (64 chars)'}
            keys = _crypto.keys_from_seed(seed, network)
            return dict(keys, seed_phrase=_mnemonic.encode(seed))
        except (_mnemonic.MnemonicError, _crypto.CryptoError, ValueError) as e:
            return _err(e)

    def subaddress(self, address: str = None, view_secret_key: str = None,
                   major: int = 0, minor: int = 1, network: str = 'mainnet') -> dict:
        """Derive a subaddress from a main address and its view key."""
        try:
            parsed = _crypto.decode_address(address)
            sub = _crypto.subaddress(bytes.fromhex(view_secret_key),
                                     bytes.fromhex(parsed['spend_public_key']),
                                     int(major), int(minor), parsed['network'])
            return {'subaddress': sub, 'major': int(major), 'minor': int(minor),
                    'base_address': parsed['address']}
        except (_crypto.CryptoError, ValueError, TypeError) as e:
            return _err(e)

    def integrated(self, address: str, payment_id: str = None) -> dict:
        """Fold an 8-byte payment id into an address."""
        try:
            parsed = _crypto.decode_address(address)
            pid = bytes.fromhex(payment_id) if payment_id else _crypto.random_payment_id()
            out = _crypto.integrated_address(
                bytes.fromhex(parsed['spend_public_key']),
                bytes.fromhex(parsed['view_public_key']), pid, parsed['network'])
            return {'integrated_address': out, 'payment_id': pid.hex(),
                    'base_address': parsed['address'],
                    'note': 'Subaddresses are usually the better choice: they are '
                            'unlinkable, while an integrated address reveals the '
                            'base address it was built from.'}
        except (_crypto.CryptoError, ValueError) as e:
            return _err(e)

    # ── Local wallets ──────────────────────────────────────────────────────

    def wallet_create(self, name: str, password: str, network: str = 'mainnet',
                      restore_height: int = None) -> dict:
        """Create a wallet. Returns the seed phrase exactly once."""
        try:
            if restore_height is None:
                try:
                    restore_height = self.daemon.tip_height()
                except _daemon.DaemonError:
                    restore_height = None
            return _wallet.create(name, password, None, network, restore_height)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_restore(self, name: str, password: str, seed_phrase: str,
                       network: str = 'mainnet', restore_height: int = None) -> dict:
        """Restore a wallet from its 25-word seed phrase."""
        try:
            return _wallet.create(name, password, seed_phrase, network, restore_height)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_watch(self, name: str, password: str, address: str,
                     view_secret_key: str, restore_height: int = None) -> dict:
        """Add a view-only wallet: an address plus its private view key."""
        try:
            return _wallet.import_view_only(name, password, address,
                                            view_secret_key, restore_height)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_list(self) -> dict:
        """Wallets on this host."""
        return {'wallets': _wallet.list_wallets(), 'dir': str(_wallet.wallet_dir())}

    def wallet_info(self, name: str) -> dict:
        """Addresses and metadata (no password needed)."""
        try:
            return _wallet.info(name)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_new_address(self, name: str, password: str, label: str = '',
                           major: int = 0) -> dict:
        """Derive the next subaddress -- the right way to take a new payment."""
        try:
            return _wallet.new_subaddress(name, password, label, int(major))
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_integrated(self, name: str, payment_id: str = None) -> dict:
        """An integrated address for this wallet."""
        try:
            return _wallet.make_integrated(name, payment_id)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_label(self, name: str, address: str, label: str) -> dict:
        """Label one of the wallet's subaddresses."""
        try:
            return _wallet.label(name, address, label)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_reveal(self, name: str, password: str) -> dict:
        """Reveal the seed phrase and keys. Handle with care."""
        try:
            return _wallet.reveal(name, password)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_delete(self, name: str, password: str) -> dict:
        """Delete a wallet file (the password must verify)."""
        try:
            return _wallet.delete(name, password)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_restore_height(self, name: str, height: int) -> dict:
        """Set where scanning should start for this wallet."""
        try:
            return _wallet.set_restore_height(name, height)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_scan(self, name: str, password: str, start_height: int = None,
                    blocks: int = 20, subaddresses: int = 5,
                    budget_seconds: float = 120) -> dict:
        """Scan a range of blocks for outputs belonging to this wallet.

        Bounded on purpose: the whole chain is 3.7 million blocks and this is
        Python. Start at the wallet's restore height, take a window at a time,
        and use the reported rate to judge how big a window is worth asking
        for. The view key stays on this host.
        """
        try:
            secrets = _wallet.secrets(name, password)
        except _wallet.WalletError as e:
            return _err(e)
        if not secrets.get('view_secret_key'):
            return {'error': f'wallet {name!r} holds no view key'}

        if start_height is None:
            info = _wallet.info(name)
            start_height = info.get('restore_height')
            if start_height is None:
                try:
                    start_height = max(0, self.daemon.tip_height() - int(blocks))
                except _daemon.DaemonError as e:
                    return _err(e)
        try:
            result = _scan.scan_blocks(
                self.daemon, bytes.fromhex(secrets['view_secret_key']),
                bytes.fromhex(secrets['spend_public_key']), int(start_height),
                int(blocks), secrets.get('network', 'mainnet'),
                accounts=1, subaddresses=int(subaddresses),
                budget_seconds=budget_seconds)
        except (_daemon.DaemonError, _scan.ScanError, ValueError) as e:
            return _err(e)
        result['wallet'] = name
        result['next_start_height'] = result['to_height'] + 1
        return result

    # ── Spending, via monero-wallet-rpc ────────────────────────────────────

    def rpc_status(self) -> dict:
        """Is a monero-wallet-rpc reachable, and which wallet is open?"""
        return self.rpc.status()

    def balance(self, account: int = 0) -> dict:
        """Spendable balance from monero-wallet-rpc.

        This is the real one: unlike a scan it knows which outputs have been
        spent, because the wallet holds the key images.
        """
        try:
            return self.rpc.balance(account)
        except _walletrpc.WalletRPCError as e:
            return _err(e)

    def transfers(self, incoming: bool = True, outgoing: bool = True,
                  pending: bool = True, failed: bool = False,
                  account: int = 0) -> dict:
        """Payment history from monero-wallet-rpc."""
        try:
            return self.rpc.transfers(incoming, outgoing, pending, failed,
                                      account=account)
        except _walletrpc.WalletRPCError as e:
            return _err(e)

    def send(self, to: str, amount: float = None, broadcast: bool = False,
             priority: int = 1, account: int = 0, payment_id: str = None,
             amount_atomic: int = None, sweep: bool = False) -> dict:
        """Send XMR.

        Dry run by default: the transaction is built and fully signed by
        monero-wallet-rpc but not relayed, and the fee, weight and hash below
        are the ones the network would see. Pass broadcast=True to publish, or
        feed the returned tx_metadata to send_confirm.
        """
        try:
            _crypto.decode_address(to)
        except _crypto.CryptoError as e:
            return {'error': f'refusing to send to an invalid address: {e}'}

        try:
            if sweep:
                result = self.rpc.sweep_all(to, priority, account, relay=broadcast)
                fee = sum(result.get('fee_list') or [])
                amounts = sum(result.get('amount_list') or [])
                txids = result.get('tx_hash_list') or []
                metadata = result.get('tx_metadata_list') or []
            else:
                if amount_atomic is None:
                    if amount is None:
                        return {'error': 'specify amount (XMR) or amount_atomic'}
                    amount_atomic = _daemon.piconero(amount)
                if int(amount_atomic) <= 0:
                    return {'error': 'amount must be positive'}
                result = self.rpc.transfer(
                    [{'address': to, 'amount': int(amount_atomic)}],
                    priority=priority, account=account, relay=broadcast,
                    payment_id=payment_id)
                fee = result.get('fee') or 0
                amounts = result.get('amount') or int(amount_atomic)
                txids = [result.get('tx_hash')] if result.get('tx_hash') else []
                metadata = [result.get('tx_metadata')] if result.get('tx_metadata') else []
        except _walletrpc.WalletRPCError as e:
            return _err(e)

        out = {
            'to': to, 'sweep': bool(sweep),
            'amount': amounts, 'amount_xmr': _daemon.xmr(amounts),
            'fee': fee, 'fee_xmr': _daemon.xmr(fee),
            'txids': txids, 'transactions': len(txids) or 1,
            'weight': result.get('weight'), 'priority': priority,
            'tx_metadata': metadata,
        }
        if broadcast:
            out['broadcast'] = True
            out['mode'] = 'BROADCAST'
            out['note'] = (f"Relayed. Monero transactions are final once mined; "
                           f"there is no way to recall this.")
        else:
            out['broadcast'] = False
            out['mode'] = 'DRY RUN'
            out['note'] = (
                "DRY RUN -- nothing was relayed and no funds moved. The "
                "transaction above is real and signed; the fee and weight are "
                "exact. Re-run with broadcast=True, or publish this exact "
                "transaction with send_confirm(tx_metadata=...).")
        return out

    def send_confirm(self, tx_metadata: str) -> dict:
        """Relay a transaction previewed earlier by send().

        Publishes the exact transaction that was shown, rather than building a
        second one that might differ in fee or inputs.
        """
        try:
            result = self.rpc.relay(tx_metadata)
        except _walletrpc.WalletRPCError as e:
            return _err(e)
        return {'txid': result.get('tx_hash'), 'broadcast': True,
                'mode': 'BROADCAST', 'relayed_previewed_transaction': True}

    def sweep(self, to: str, broadcast: bool = False, priority: int = 1,
              account: int = 0) -> dict:
        """Send everything unlocked in an account to one address."""
        return self.send(to, broadcast=broadcast, priority=priority,
                         account=account, sweep=True)

    def broadcast_raw(self, tx_hex: str) -> dict:
        """Push an already-signed transaction to the network through the node."""
        try:
            result = self.daemon.broadcast(tx_hex.strip())
            return dict(result, broadcast=True)
        except _daemon.DaemonError as e:
            return _err(e)

    def rpc_open(self, filename: str, password: str = '') -> dict:
        """Open a wallet file in the running monero-wallet-rpc."""
        try:
            return self.rpc.open_wallet(filename, password)
        except _walletrpc.WalletRPCError as e:
            return _err(e)

    def rpc_load_wallet(self, name: str, password: str, rpc_password: str = '',
                        filename: str = None) -> dict:
        """Hand one of this module's wallets to monero-wallet-rpc so it can spend.

        The seed phrase is decrypted here and passed to the local wallet RPC
        over loopback. It is the one moment the phrase leaves the encrypted
        file, so the RPC should be one you run yourself.
        """
        try:
            secrets = _wallet.secrets(name, password)
        except _wallet.WalletError as e:
            return _err(e)
        if not secrets.get('seed_phrase'):
            return {'error': f'wallet {name!r} is view-only and has no seed phrase'}
        info = _wallet.info(name)
        try:
            result = self.rpc.restore_wallet(
                filename or name, secrets['seed_phrase'], rpc_password,
                info.get('restore_height') or 0)
        except _walletrpc.WalletRPCError as e:
            return _err(e)
        return dict(result, wallet=name, address=info['address'],
                    note='monero-wallet-rpc now holds this wallet and will scan '
                         'from its restore height. Balance and send work once it '
                         'has caught up.')

    def key_images(self) -> dict:
        """Export key images from monero-wallet-rpc.

        These are what a view-only wallet needs to know what has already been
        spent -- the one thing view-key scanning cannot work out for itself.
        """
        try:
            result = self.rpc.export_key_images()
        except _walletrpc.WalletRPCError as e:
            return _err(e)
        images = result.get('signed_key_images') or []
        return {'count': len(images), 'offset': result.get('offset'),
                'signed_key_images': images}

    # ── Swaps ──────────────────────────────────────────────────────────────

    def bridge_routes(self) -> dict:
        """How XMR can and cannot leave Monero."""
        return _bridge.routes()

    def bridge_assets(self, search: str = None, limit: int = 100) -> dict:
        """Assets XMR can be swapped against."""
        try:
            return _bridge.assets(search, limit)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_quote(self, to_asset: str, amount: float,
                     from_asset: str = 'XMR', rate_type: str = 'float') -> dict:
        """Price a swap without reserving anything.

        Assets are 'BTC', 'ETH', or chain-qualified as 'TRX:USDT', 'ETH:USDC'.
        """
        try:
            return _bridge.quote(from_asset, to_asset, amount, rate_type)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_start(self, to_asset: str, amount: float, recipient: str,
                     refund_to: str, from_asset: str = 'XMR',
                     rate_type: str = 'float', recipient_memo: str = None) -> dict:
        """Reserve a deposit address for a swap.

        Nothing moves until you fund it. The provider takes custody in
        between -- see bridge_routes() for why there is no trustless option.
        """
        try:
            return _bridge.swap_start(from_asset, to_asset, amount, recipient,
                                      refund_to, rate_type, recipient_memo)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_status(self, order_id: str) -> dict:
        """Track a swap."""
        try:
            return _bridge.swap_status(order_id)
        except _bridge.BridgeError as e:
            return _err(e)

    # ── Meta ───────────────────────────────────────────────────────────────

    def capabilities(self) -> dict:
        """What works, what needs help, and what this module refuses to fake."""
        rpc = self.rpc.status()
        node = self.daemon.node_info()
        return {
            'explorer': {
                'supported': True,
                'details': 'blocks, transactions, mempool, fees and ring members '
                           'from a Monero node, with xmrchain.net as fallback',
            },
            'keys_and_addresses': {
                'supported': True,
                'details': '25-word seed phrases, standard/sub/integrated '
                           'addresses, all derived in pure Python',
            },
            'wallet_storage': {
                'supported': True,
                'details': 'AES-256-GCM at rest behind PBKDF2-SHA256 (600k), '
                           'full or view-only, in ~/.mod/monero/wallets',
            },
            'view_key_scanning': {
                'supported': True,
                'details': 'finds your own outputs locally, view tags first; '
                           'bounded by block window, roughly 0.3 blocks/second '
                           'through a public node',
            },
            'spent_detection': {
                'supported': False,
                'reason': 'Knowing an output was spent means computing its key '
                          'image, which needs the private spend key and the '
                          'hash-to-point map. This module will not ship an '
                          'unverifiable implementation of that -- a wrong one '
                          'reports a wrong balance silently.',
                'workaround': 'monero-wallet-rpc holds the key images; `balance` '
                              'reads the true spendable amount from it.',
            },
            'building_transactions': {
                'supported': False,
                'reason': 'A spend needs a CLSAG signature over a 16-member ring '
                          'and a Bulletproofs+ range proof. In pure Python that '
                          'is impractically slow and impossible to verify against '
                          'anything, and a subtly wrong proof can leak which ring '
                          'member is real.',
                'workaround': 'send/sweep drive monero-wallet-rpc, which is the '
                              'reference implementation. Dry run unless '
                              'broadcast=True.',
            },
            'sending': {
                'supported': bool(rpc.get('available')),
                'details': ('monero-wallet-rpc reachable at ' + str(rpc.get('url')))
                           if rpc.get('available') else
                           'needs monero-wallet-rpc; see rpc_status() for the '
                           'exact command to start one',
            },
            'bridge': {
                'supported': True,
                'details': 'custodial instant swap (Exolix, 630 assets, no API '
                           'key). No trustless bridge exists for Monero.',
            },
            'node': node,
            'wallet_rpc': rpc,
            'safety': 'send() and sweep() are dry runs unless broadcast=True',
        }

    def token(self) -> dict:
        """The bearer token the web app needs for wallet and spending functions."""
        state = Path(os.environ.get('MONERO_STATE_DIR')
                     or Path.home() / '.mod' / 'monero')
        state.mkdir(parents=True, exist_ok=True)
        path = state / 'server.secret'
        if not path.exists():
            import secrets as _secrets
            path.write_text(_secrets.token_hex(32))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return {'token': path.read_text().strip(), 'path': str(path),
                'use': "paste into the app's unlock field, or send as "
                       "'Authorization: Bearer <token>' to the REST API"}

    def status(self) -> dict:
        """Service and chain status."""
        out = {'api': {'port': self.api_port}, 'app': {'port': self.app_port},
               'rest': {'port': self.rest_port}}
        for svc, port in (('api', self.api_port), ('app', self.app_port),
                          ('rest', self.rest_port)):
            try:
                r = requests.get(f'http://localhost:{port}', timeout=3)
                out[svc].update(running=True, code=r.status_code)
            except requests.RequestException:
                out[svc].update(running=False)
        try:
            info = self.daemon.info()
            out['chain'] = {'height': info.get('height'), 'network': info.get('network'),
                            'hard_fork_version': info.get('hard_fork_version'),
                            'source': info.get('source')}
        except _daemon.DaemonError as e:
            out['chain'] = _err(e)
        out['wallets'] = len(_wallet.list_wallets())
        out['wallet_rpc'] = self.rpc.status().get('available')
        # The MCP endpoint rides on the REST server, so it is up exactly when
        # that is -- say so rather than making a caller guess.
        try:
            out['mcp'] = {'tools': len(self._mcp().TOOLS),
                          'url': f'http://127.0.0.1:{self.rest_port}/mcp',
                          'stdio': f'python3 {self._dir / "mcp.py"}',
                          'running': out['rest'].get('running')}
        except Exception as e:
            out['mcp'] = _err(e)
        return out

    def test(self) -> dict:
        """Self-test: primitives, seed phrases, the scanner, and the network."""
        results, failures = {}, []

        for name, fn in (('crypto', _crypto.self_test),
                         ('mnemonic', _mnemonic.self_test),
                         ('scanner', _scan.self_test)):
            try:
                results[name] = fn()
            except Exception as e:
                results[name] = {'ok': False, 'error': str(e)}
            if not results[name].get('ok'):
                failures.append(name)

        # A wallet round trip, in a directory that is thrown away afterwards.
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix='monero-selftest-')
        previous = os.environ.get('MONERO_WALLET_DIR')
        os.environ['MONERO_WALLET_DIR'] = tmp
        try:
            created = _wallet.create('selftest', 'password')
            restored_ok = False
            _wallet.delete('selftest', 'password')
            _wallet.create('selftest', 'password', created['seed_phrase'])
            restored_ok = _wallet.info('selftest')['address'] == created['address']
            results['wallet'] = {'ok': restored_ok,
                                 'seed_phrase_restores_same_address': restored_ok}
        except Exception as e:
            results['wallet'] = {'ok': False, 'error': str(e)}
        finally:
            if previous is None:
                os.environ.pop('MONERO_WALLET_DIR', None)
            else:
                os.environ['MONERO_WALLET_DIR'] = previous
            shutil.rmtree(tmp, ignore_errors=True)
        if not results['wallet'].get('ok'):
            failures.append('wallet')

        try:
            info = self.daemon.info()
            results['chain'] = {'ok': bool(info.get('height')),
                                'height': info.get('height'),
                                'source': info.get('source'),
                                'node': self.daemon.node_info().get('url')}
        except _daemon.DaemonError as e:
            results['chain'] = {'ok': False, 'error': str(e)}
        if not results['chain'].get('ok'):
            failures.append('chain')

        try:
            quote = _bridge.quote('XMR', 'BTC', 1)
            results['bridge'] = {'ok': bool(quote.get('amount_out')),
                                 'xmr_btc': quote.get('rate')}
        except _bridge.BridgeError as e:
            results['bridge'] = {'ok': False, 'error': str(e)}
        if not results['bridge'].get('ok'):
            failures.append('bridge')

        rpc = self.rpc.status()
        results['wallet_rpc'] = {
            'ok': True, 'available': rpc.get('available'),
            'note': None if rpc.get('available') else
                    'not running -- everything except send/sweep/balance still works'}

        results['status'] = 'ok' if not failures else 'degraded'
        results['failed'] = failures
        return results

    # ── The MCP server ─────────────────────────────────────────────────────

    def _mcp(self):
        """mcp.py, by path -- `import mcp` would find whatever is on sys.path."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'monero_mcp', self._dir / 'mcp.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def mcp(self, url: str = None) -> dict:
        """The MCP server described: transports, auth, tool names, client config."""
        doc = self._mcp().describe(url)
        doc['tools'] = [{'name': t['name'], 'auth': t['auth'],
                         'summary': t['description'].split('. ')[0] + '.'}
                        for t in doc['tools']]
        return doc

    def mcp_tools(self, name: str = None) -> dict:
        """The full tool schemas -- what `tools/list` returns, one or all."""
        tools = self._mcp().describe()['tools']
        if name:
            tools = [t for t in tools if t['name'] in (name, f'xmr_{name}')]
            if not tools:
                return {'error': f'no such tool: {name}'}
        return {'count': len(tools), 'tools': tools}

    def mcp_call(self, tool: str, **kw):
        """Run one MCP tool as this box does over stdio -- no token needed.

        The gate already decided this caller may reach the module at all, so a
        second token here would only lock the owner out of their own tools.
        """
        mcp = self._mcp()
        name = tool if tool in mcp.TOOLS else f'xmr_{tool}'
        try:
            return mcp.call_tool(name, kw, mcp.LOCAL_CTX)
        except Exception as e:
            return {'error': f'{name}: {e}'}

    # ── Serve ──────────────────────────────────────────────────────────────

    def rest_up(self, port=None) -> bool:
        """Is the app's REST backend answering on its port?"""
        port = int(port or self.rest_port)
        try:
            return requests.get(f'http://127.0.0.1:{port}/health', timeout=2).ok
        except requests.RequestException:
            return False

    # pm2 keeps the REST backend and the app alive across restarts and reboots.
    # When an entry exists, drive it through pm2 -- a bare Popen here would race
    # the supervisor for the port, and a bare SIGTERM would just be restarted.
    PM2 = {'rest': 'monero-api', 'app': 'monero-app'}

    def _pm2_names(self) -> set:
        try:
            r = subprocess.run(['pm2', 'jlist'], capture_output=True,
                               text=True, timeout=15)
            return {p.get('name') for p in json.loads(r.stdout or '[]')}
        except (subprocess.SubprocessError, OSError, ValueError, TypeError):
            return set()

    def _pm2_do(self, name, action) -> bool:
        try:
            return subprocess.run(['pm2', action, name], capture_output=True,
                                  text=True, timeout=120).returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def _start_rest(self, port, wait=25) -> dict:
        """Start api.py unless it is already up, and wait for it to answer.

        The app and the MCP endpoint are both this process, so starting it is
        not fire-and-forget: report whether it actually came up rather than
        claiming success and leaving the page to 503.
        """
        port = int(port)
        if self.rest_up(port):
            return {'running': True, 'started': False, 'port': port}

        if self.PM2['rest'] in self._pm2_names():
            self._pm2_do(self.PM2['rest'], 'restart')
        else:
            env = os.environ.copy()
            env['PYTHONPATH'] = str(self._dir)
            env['PORT'] = env['MONERO_REST_PORT'] = str(port)
            log = open(self._log_dir / 'rest.log', 'a')
            subprocess.Popen(['python3', 'api.py'], cwd=str(self._dir), env=env,
                             stdout=log, stderr=subprocess.STDOUT)

        deadline = time.time() + wait
        while time.time() < deadline:
            if self.rest_up(port):
                return {'running': True, 'started': True, 'port': port}
            time.sleep(0.5)
        return {'running': False, 'started': True, 'port': port,
                'error': f'api.py did not answer on :{port} within {wait}s '
                         f'-- see {self._log_dir / "rest.log"}'}

    def serve(self, rest_port=None, app_port=None, dev=False):
        """Start the local REST API (and its MCP endpoint) and the web app.

        The mod-protocol server on port 50690 is managed by the fleet; this
        starts the app's own backend (api.py) and the Next front end.
        """
        rest_port = int(rest_port or self.rest_port)
        app_port = int(app_port or self.app_port)
        self.kill()
        rest = self._start_rest(rest_port)

        self.app(port=app_port, dev=dev, rest_port=rest_port)
        return {'rest_api': f'http://127.0.0.1:{rest_port}',
                'rest': rest,
                'mcp': f'http://127.0.0.1:{rest_port}/mcp',
                'app': f'http://localhost:{app_port}/monero',
                'mod_protocol_api': f'http://localhost:{self.api_port}',
                'dev': dev, 'logs': str(self._log_dir)}

    def app(self, port=None, dev=False, rest_port=None):
        """Start the web app (and its REST backend, if that is not up yet)."""
        port = int(port or self.app_port)
        rest_port = int(rest_port or self.rest_port)
        app_dir = self._dir / 'app'
        if not app_dir.exists():
            return {'error': 'app directory not found'}
        # A front end pointed at a dead backend is the module's classic broken
        # state; make sure the backend exists before advertising a URL.
        rest = self._start_rest(rest_port)

        # Supervised: let pm2 own the process. `dev` is deliberately ignored
        # here -- a `next dev` sharing the live .next/ leaves a dev build
        # behind and the served page loses every chunk.
        if not dev and self.PM2['app'] in self._pm2_names():
            self._pm2_do(self.PM2['app'], 'restart')
            return {'status': 'running', 'port': port, 'rest': rest,
                    'supervisor': f"pm2:{self.PM2['app']}",
                    'url': f'http://localhost:{port}/monero'}

        app_log = open(self._log_dir / 'app.log', 'w')
        env = os.environ.copy()
        env['MONERO_API_ORIGIN'] = f'http://127.0.0.1:{rest_port}'
        # so the app's /api route can restart api.py if it ever dies
        env['MONERO_MODULE_DIR'] = str(self._dir)
        env.setdefault('NEXT_PUBLIC_BASE_PATH', '/monero')
        cmd = ['npx', 'next', 'dev', '-p', str(port)] if dev else \
              ['npx', 'next', 'start', '-p', str(port)]
        subprocess.Popen(cmd, cwd=str(app_dir), env=env,
                         stdout=app_log, stderr=subprocess.STDOUT)
        return {'status': 'running', 'port': port, 'rest': rest,
                'url': f"http://localhost:{port}{env['NEXT_PUBLIC_BASE_PATH']}",
                'logs': str(self._log_dir / 'app.log')}

    def kill(self, service=None):
        """Stop this module's services. Only its own, and only by port or pm2.

        Around twenty modules on this host share a `next-server` process name,
        so killing by name would take the fleet down with it.
        """
        killed = []
        targets = [service] if service else ['rest', 'app']
        supervised = self._pm2_names()
        for svc in targets:
            port = self.rest_port if svc == 'rest' else self.app_port
            # Stop through pm2 where pm2 owns it; SIGTERMing it directly just
            # gets it restarted a second later and reads as "kill did nothing".
            name = self.PM2.get(svc)
            if name in supervised:
                if self._pm2_do(name, 'stop'):
                    killed.append(f'{svc}:pm2:{name}')
                continue
            for pid in _pids_on_port(port):
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(f'{svc}:{pid}')
                except OSError:
                    continue
        return {'killed': killed}
