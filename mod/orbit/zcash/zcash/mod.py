"""
Zcash: explorer, wallet (transparent and shielded), and cross-chain bridge.

  explorer   info / block / tx / address / mempool / price / network / search
  wallet     create, restore, import keys, derive addresses, balances
  send       build, sign (ZIP-244) and broadcast transparent transactions
  shielded   real Sapling and Orchard addresses, note decryption, exports
  bridge     ZEC <-> Ethereum and 30+ other chains via NEAR Intents / Maya

Spending is guarded: `send` and `bridge_send` are dry runs unless you pass
broadcast=True, and every response says plainly which mode it ran in.

The shielded half is real on both sides. It derives ZIP-32 Sapling *and*
Orchard keys from the same seed as the transparent ones, hands out `zs1` and
unified addresses carrying both shielded receivers, and decrypts the notes and
actions those addresses receive -- all in pure Python. Spending needs a
zk-SNARK proof, which Python will not produce, so a local light client does
it: `shielded_backend_install` builds it once, `shielded_sync_start` scans the
chain for this wallet's notes, and `shielded_send` proves and broadcasts. No
full node. See `capabilities()`.
"""

import json
import os
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
    from . import agent as _agent
    from . import bridge as _bridge
    from . import bundles as _bundles
    from . import chain as _chain
    from . import keys as _keys
    from . import learn as _learn
    from . import lightclient as _lightclient
    from . import orchard as _orchard
    from . import sapling as _sapling
    from . import shielded as _shielded
    from . import tx as _tx
    from . import wallet as _wallet
except ImportError:  # loaded as a loose module by the mod runtime
    import agent as _agent
    import bridge as _bridge
    import bundles as _bundles
    import chain as _chain
    import keys as _keys
    import learn as _learn
    import lightclient as _lightclient
    import orchard as _orchard
    import sapling as _sapling
    import shielded as _shielded
    import tx as _tx
    import wallet as _wallet

ZAT = 100_000_000


def _zec(zatoshi) -> float:
    return (zatoshi or 0) / ZAT


def _err(e) -> dict:
    return {"error": str(e), "error_type": type(e).__name__}


# Zcash's supply cap, for context on circulating supply.
MAX_SUPPLY_ZEC = 21_000_000


def _supply(s: dict) -> dict:
    """Circulating supply, guarding Blockchair's broken `circulation` field.

    Blockchair currently reports a *negative* circulation for Zcash while its
    own market cap is computed from a sane supply, so passing the raw field
    through puts an impossible number (-5,026,080 ZEC) on screen. Use it when
    it is plausible; otherwise fall back to the supply implied by cap / price
    and say so, and report nothing at all if neither holds up.
    """
    raw = s.get('circulation')
    zec = (raw or 0) / ZAT
    if raw and 0 < zec <= MAX_SUPPLY_ZEC:
        return {'circulation': raw, 'circulation_zec': zec,
                'circulation_source': 'blockchair'}

    cap, price = s.get('market_cap_usd'), s.get('market_price_usd')
    try:
        implied = float(cap) / float(price)
    except (TypeError, ValueError, ZeroDivisionError):
        implied = 0
    if 0 < implied <= MAX_SUPPLY_ZEC:
        return {'circulation': round(implied * ZAT), 'circulation_zec': implied,
                'circulation_source': 'implied from market cap / price '
                                      '(blockchair reports an invalid supply)'}

    return {'circulation': None, 'circulation_zec': None,
            'circulation_source': 'unavailable'}


class Mod:
    description = ("Zcash explorer, wallet (transparent sends + Sapling and "
                   "Orchard shielded addresses and note decryption) and "
                   "cross-chain bridge")

    fns = [
        # explorer
        'info', 'block', 'tx', 'address', 'mempool', 'price', 'network', 'search',
        # wallet
        'wallet_create', 'wallet_restore', 'wallet_list', 'wallet_info',
        'wallet_new_address', 'wallet_import', 'wallet_balance', 'wallet_utxos',
        'wallet_reveal', 'wallet_delete', 'wallet_label',
        # shielded (Sapling + Orchard)
        'shielded_address', 'shielded_new_address', 'shielded_upgrade',
        'shielded_export', 'shielded_scan', 'shielded_balance',
        'shielded_scan_tx', 'shielded_send', 'shielded_node_import',
        'shielded_operation',
        # shielded spending (the local prover)
        'shielded_backend', 'shielded_backend_install', 'shielded_sync_start',
        'shielded_sync_status', 'shielded_sync_stop', 'shielded_spendable',
        'shielded_shield',
        # spending
        'validate', 'estimate_fee', 'send', 'broadcast_raw',
        # bridge
        'bridge_chains', 'bridge_quote', 'bridge_start', 'bridge_status',
        'bridge_send', 'bridge_maya',
        # meta
        'capabilities', 'status', 'token', 'mcp', 'serve', 'app', 'kill', 'test',
    ]

    # The local REST server the web app talks to, and where /mcp is served
    # (see api.py).
    rest_port = int(os.environ.get('ZCASH_REST_PORT', 8930))

    _mcp_module = None

    api_base = "https://api.blockchair.com/zcash"

    def __init__(self, config=None, **kwargs):
        self._dir = Path(__file__).parent.parent
        self._log_dir = Path('/tmp/zcash')
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._config = config or self._load_config()
        self._load_env()
        self.api_port = int(self._config.get('port', 50148))
        self.app_port = int(self._config.get('app_port', 50149))
        self._chain = None

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
    def chain(self):
        if self._chain is None:
            self._chain = _chain.Chain()
        return self._chain

    def _get(self, url: str, params: dict = None) -> dict:
        try:
            r = requests.get(url, params=params, timeout=20)
            # Blockchair answers 402 (and sometimes 429) once the keyless quota
            # for this host is spent. Raised verbatim it reads as "Payment
            # Required", which sounds like the module wants money.
            if r.status_code in (402, 429):
                return {'error': 'blockchair rate limit reached for this host -- '
                                 'set BLOCKCHAIR_API_KEY, or ZCASH_RPC_URL to read '
                                 'from your own node',
                        'rate_limited': True}
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            return {'error': 'Request timed out'}
        except requests.exceptions.RequestException as e:
            return {'error': str(e)}

    def forward(self, **kwargs):
        return self.info()

    # ── Explorer ───────────────────────────────────────────────────────────

    # info/price/network/mempool all read the same /stats document, and the app
    # polls them together, so an uncached page load spent three requests of a
    # small public quota on identical data -- enough to get rate-limited into an
    # empty explorer. Blocks arrive every ~75s; 30s of staleness costs nothing.
    _stats_cache = (0.0, None)
    STATS_TTL = 30
    STALE_MAX = 900          # keep serving a known-good copy for 15 minutes

    def _stats(self) -> dict:
        """Blockchair's /stats document, cached briefly. May return {'error'}."""
        at, cached = Mod._stats_cache
        age = time.time() - at
        if cached is not None and age < self.STATS_TTL:
            return cached
        data = self._get(f"{self.api_base}/stats")
        if 'error' in data:
            # Through a rate limit or a blip, last-known-good beats a blank
            # page -- as long as it is labelled. Never cache the failure.
            if cached is not None and age < self.STALE_MAX:
                return dict(cached, _stale_seconds=int(age), _stale_reason=data['error'])
            return data
        Mod._stats_cache = (time.time(), data)
        return data

    @staticmethod
    def _staleness(data: dict) -> dict:
        """Staleness markers to merge into an explorer response, if any."""
        if '_stale_seconds' not in data:
            return {}
        return {'stale': True, 'stale_seconds': data['_stale_seconds'],
                'stale_reason': data.get('_stale_reason')}

    def info(self) -> dict:
        """Zcash blockchain overview stats."""
        data = self._stats()
        if 'error' in data:
            return data
        s = data.get('data', {})
        return {
            'blocks': s.get('blocks'),
            'transactions': s.get('transactions'),
            'difficulty': s.get('difficulty'),
            'hashrate': s.get('hashrate_24h'),
            'market_price_usd': s.get('market_price_usd'),
            'market_cap_usd': s.get('market_cap_usd'),
            **_supply(s),
            'max_supply_zec': MAX_SUPPLY_ZEC,
            'mempool_transactions': s.get('mempool_transactions'),
            'mempool_size': s.get('mempool_size'),
            'best_block_height': s.get('best_block_height'),
            'best_block_hash': s.get('best_block_hash'),
            'best_block_time': s.get('best_block_time'),
            **self._staleness(data),
        }

    def block(self, height: int = None, hash: str = None) -> dict:
        """Block details by height or hash; latest when neither is given."""
        if hash:
            target = hash
        elif height is not None:
            target = height
        else:
            stats = self.info()
            if 'error' in stats:
                return stats
            target = stats.get('best_block_height')
        data = self._get(f"{self.api_base}/dashboards/block/{target}")
        if 'error' in data:
            return data
        blocks = data.get('data') or {}
        if not blocks:
            return {'error': 'Block not found'}
        b = blocks[list(blocks.keys())[0]].get('block', {})
        return {
            'height': b.get('id'), 'hash': b.get('hash'), 'time': b.get('time'),
            'size': b.get('size'), 'transaction_count': b.get('transaction_count'),
            'input_total': b.get('input_total'), 'output_total': b.get('output_total'),
            'difficulty': b.get('difficulty'), 'reward': b.get('reward'),
        }

    def tx(self, txid: str) -> dict:
        """Transaction details by txid."""
        data = self._get(f"{self.api_base}/dashboards/transaction/{txid}")
        if 'error' in data:
            return data
        txs = data.get('data') or {}
        if not txs:
            return {'error': 'Transaction not found'}
        t = txs[list(txs.keys())[0]].get('transaction', {})
        spends = t.get('shielded_input_raw') or []
        outputs = t.get('shielded_output_raw') or []
        out = {
            'hash': t.get('hash'), 'block_id': t.get('block_id'), 'time': t.get('time'),
            'size': t.get('size'), 'fee': t.get('fee'),
            'input_total': t.get('input_total'), 'output_total': t.get('output_total'),
            'input_count': t.get('input_count'), 'output_count': t.get('output_count'),
            'is_coinbase': t.get('is_coinbase'),
            'version': t.get('version'),
            'sapling_spends': len(spends),
            'sapling_outputs': len(outputs),
            'shielded_value_delta': t.get('shielded_value_delta'),
            'has_shielded': bool(spends or outputs
                                 or t.get('shielded_value_delta')),
        }
        if outputs:
            out['note'] = (
                f"{len(outputs)} shielded output(s): the amounts and "
                f"recipients are encrypted. shielded_scan_tx will open the "
                f"ones a viewing key of yours can read.")
        return out

    def address(self, addr: str) -> dict:
        """Balance and activity for a transparent address."""
        try:
            return self.chain.balance(addr)
        except _chain.ChainError as e:
            return _err(e)

    def mempool(self) -> dict:
        """Current mempool stats."""
        stats = self.info()
        if 'error' in stats:
            return stats
        return {'transactions': stats.get('mempool_transactions'),
                'size': stats.get('mempool_size')}

    def price(self) -> dict:
        """Current ZEC price and market data."""
        data = self._stats()
        if 'error' in data:
            return data
        s = data.get('data', {})
        return {'price_usd': s.get('market_price_usd'),
                'market_cap_usd': s.get('market_cap_usd'),
                'market_dominance': s.get('market_dominance'),
                'max_supply_zec': MAX_SUPPLY_ZEC,
                **_supply(s), **self._staleness(data)}

    def network(self) -> dict:
        """Network health and mining stats."""
        data = self._stats()
        if 'error' in data:
            return data
        s = data.get('data', {})
        out = {
            'blocks': s.get('blocks'), 'difficulty': s.get('difficulty'),
            'hashrate_24h': s.get('hashrate_24h'),
            'best_block_height': s.get('best_block_height'),
            'best_block_time': s.get('best_block_time'),
            'mempool_transactions': s.get('mempool_transactions'),
            'nodes': s.get('nodes'), 'blockchain_size': s.get('blockchain_size'),
            **self._staleness(data),
        }
        try:
            out['consensus_branch_id'] = f"{self.chain.consensus_branch_id():08x}"
        except _chain.ChainError:
            pass
        return out

    def search(self, query: str) -> dict:
        """Find a block, transaction or address."""
        q = (query or "").strip()
        if not q:
            return {'error': 'empty query'}
        if q.isdigit():
            return {'type': 'block', 'result': self.block(height=int(q))}
        if len(q) == 64:
            result = self.tx(txid=q)
            if 'error' not in result:
                return {'type': 'transaction', 'result': result}
            return {'type': 'block', 'result': self.block(hash=q)}
        try:
            info = _keys.decode_address(q)
        except ValueError:
            return {'error': f'Unrecognised query: {q}'}
        if info['type'] not in ('p2pkh', 'p2sh'):
            out = {'address': q, 'pool': info['type'],
                   **({'receivers': info['receivers']} if info.get('receivers') else {}),
                   'note': 'Shielded addresses have no public balance -- amounts and '
                           'recipients in the shielded pools are encrypted on chain. '
                           'Use shielded_scan with a viewing key to read your own.'}
            # A unified address may publish a transparent receiver, and that
            # half of it does have a public balance.
            if info.get('transparent_address'):
                out['transparent_receiver'] = self.address(
                    addr=info['transparent_address'])
            return {'type': 'address', 'result': out}
        return {'type': 'address', 'result': self.address(addr=q)}

    # ── Wallet ─────────────────────────────────────────────────────────────

    def wallet_create(self, name: str, password: str, addresses: int = 1,
                      strength: int = 256, passphrase: str = "") -> dict:
        """Create a new HD wallet, transparent and shielded.

        Returns the mnemonic exactly once. The wallet's birthday is set to the
        current chain height so a shielded scan knows where to start.
        """
        try:
            return _wallet.create(name, password, None, passphrase, strength,
                                  addresses, birthday=self._birthday())
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_restore(self, name: str, password: str, mnemonic: str,
                       addresses: int = 1, passphrase: str = "",
                       birthday: int = None) -> dict:
        """Restore a wallet from a BIP39 mnemonic.

        Pass `birthday` -- the height the wallet first received funds -- if you
        know it: a shielded scan has to start somewhere, and without it the
        default is today, which will not find older notes.
        """
        try:
            return _wallet.create(name, password, mnemonic, passphrase, 256,
                                  addresses, birthday=birthday or self._birthday())
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def _birthday(self):
        """Current tip, or None if the chain is unreachable (never fatal)."""
        try:
            return self.chain.tip_height()
        except Exception:
            return None

    def wallet_list(self) -> dict:
        """All wallets on this host."""
        return {'wallets': _wallet.list_wallets(), 'dir': str(_wallet.wallet_dir())}

    def wallet_info(self, name: str) -> dict:
        """Addresses and metadata for a wallet (no password needed)."""
        try:
            return _wallet.info(name)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_new_address(self, name: str, password: str, label: str = "") -> dict:
        """Derive the next receive address."""
        try:
            return _wallet.new_address(name, password, label)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_import(self, name: str, password: str, wif: str, label: str = "") -> dict:
        """Import a WIF private key, creating the wallet if it does not exist."""
        try:
            return _wallet.import_key(name, password, wif, label)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def wallet_label(self, name: str, address: str, label: str) -> dict:
        """Set a label on one of the wallet's addresses."""
        try:
            return _wallet.rename_label(name, address, label)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_reveal(self, name: str, password: str) -> dict:
        """Reveal the mnemonic and imported keys. Handle with care."""
        try:
            return _wallet.reveal(name, password)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_delete(self, name: str, password: str) -> dict:
        """Delete a wallet file (password must verify)."""
        try:
            return _wallet.delete(name, password)
        except _wallet.WalletError as e:
            return _err(e)

    def wallet_balance(self, name: str) -> dict:
        """Total confirmed balance across every address in the wallet."""
        try:
            entries = _wallet.addresses(name)
        except _wallet.WalletError as e:
            return _err(e)
        rows, total, errors = [], 0, []
        for entry in entries:
            try:
                b = self.chain.balance(entry['address'])
                total += b['balance_zatoshi']
                rows.append({'address': entry['address'], 'label': entry.get('label', ''),
                             'path': entry.get('path'),
                             'balance_zec': b['balance_zec'],
                             'balance_zatoshi': b['balance_zatoshi'],
                             'utxo_count': b['utxo_count']})
            except _chain.ChainError as e:
                errors.append({'address': entry['address'], 'error': str(e)})
        out = {'wallet': name, 'total_zec': _zec(total), 'total_zatoshi': total,
               'addresses': rows}
        if errors:
            out['errors'] = errors
        try:
            out['total_usd'] = round(_zec(total) * float(self.price().get('price_usd') or 0), 2)
        except (TypeError, ValueError):
            pass
        return out

    def wallet_utxos(self, name: str) -> dict:
        """Every spendable output held by the wallet."""
        try:
            entries = _wallet.addresses(name)
        except _wallet.WalletError as e:
            return _err(e)
        utxos = []
        for entry in entries:
            try:
                for u in self.chain.utxos(entry['address']):
                    utxos.append(dict(u, address=entry['address'], zec=_zec(u['value'])))
            except _chain.ChainError:
                continue
        return {'wallet': name, 'utxo_count': len(utxos),
                'total_zec': _zec(sum(u['value'] for u in utxos)), 'utxos': utxos}

    # ── Spending ───────────────────────────────────────────────────────────

    # ── Shielded (Sapling + Orchard) ────────────────────────────────────────

    def shielded_address(self, name: str) -> dict:
        """This wallet's shielded receive addresses. No password needed.

        A z-address is public, so listing costs nothing. Reading what has been
        paid to it does need the password -- see shielded_scan.
        """
        try:
            account = _wallet.shielded(name)
            return {
                'wallet': name,
                'account': account.get('account'),
                'birthday': account.get('birthday'),
                'addresses': account.get('addresses', []),
                'receive': account['addresses'][0]['unified_address'],
                'pools': account.get('pools', ['sapling']),
                'note': 'Give out the unified address (u1...) where you can: it '
                        'carries an Orchard and a Sapling receiver plus a '
                        'transparent one, so any wallet can pay it and a modern '
                        'one will pick Orchard. The zs1 form is the Sapling '
                        'receiver on its own.',
            }
        except _wallet.WalletError as e:
            return _err(e)

    def shielded_new_address(self, name: str, password: str, label: str = "") -> dict:
        """Derive a fresh diversified shielded address (a new payment slot)."""
        try:
            return _wallet.new_shielded_address(name, password, label)
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def shielded_upgrade(self, name: str, password: str, birthday: int = None) -> dict:
        """Give a wallet made before shielded support its shielded account."""
        try:
            return _wallet.upgrade_shielded(name, password,
                                            birthday or self._birthday())
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def shielded_export(self, name: str, password: str, account: int = None) -> dict:
        """Export this account's shielded spending and viewing keys.

        The extended spending key is what any wallet needs to *send* shielded
        ZEC with these notes. This module can spend them itself once the local
        light client is installed (shielded_send); exporting is for spending
        the same notes somewhere else -- Zashi, Ywallet, zingo, zcashd. Same
        seed, same account, same notes.
        """
        try:
            secret = _wallet.reveal(name, password)
            if not secret.get('mnemonic'):
                raise _wallet.WalletError(
                    f"wallet {name!r} has no seed; shielded keys come from one")
            acct = account
            if acct is None:
                acct = (_wallet.shielded(name) or {}).get('account', 0)
            out = _shielded.export_keys(secret['mnemonic'],
                                        secret.get('passphrase', ''), acct)
            out['wallet'] = name
            return out
        except (_wallet.WalletError, ValueError) as e:
            return _err(e)

    def shielded_scan(self, name: str, password: str, from_height: int = None,
                      to_height: int = None, blocks: int = None,
                      account: int = None, orchard: bool = True) -> dict:
        """Find this wallet's shielded notes on chain, in both pools.

        Trial-decrypts every Sapling output and every Orchard action in the
        range with the wallet's incoming viewing keys, and its own sends with
        the outgoing ones. Defaults to the wallet's birthday through the tip.

        Orchard costs more without a node: the public explorer serves Sapling
        ciphertexts inside its transaction rows but not Orchard actions, so
        each candidate transaction has to be fetched whole. Pass
        `orchard=False` for the cheap Sapling-only scan over a longer range.
        """
        try:
            xsk = _wallet.shielded_key(name, password, account)
            fvk = xsk.fvk()
            ofvk = (_wallet.orchard_key(name, password, account).fvk()
                    if orchard else None)
            tip = self.chain.tip_height()
            to_height = int(to_height) if to_height is not None else tip
            started_at = None
            if from_height is None:
                if blocks:
                    from_height = max(0, to_height - int(blocks) + 1)
                else:
                    # A wallet with no recorded birthday (an older file, or one
                    # upgraded in place) still has to start somewhere; say so
                    # rather than let it look like the whole history was read.
                    try:
                        birthday = _wallet.shielded(name).get('birthday')
                    except _wallet.WalletError:
                        birthday = None
                    window = (_shielded.ORCHARD_EXPLORER_MAX_BLOCKS if ofvk
                              is not None else _shielded.MAX_EXPLORER_BLOCKS)
                    if birthday:
                        from_height, started_at = birthday, 'wallet birthday'
                    else:
                        from_height = max(0, to_height - window + 1)
                        started_at = ('a default window -- this wallet has no '
                                      'recorded birthday, so anything paid to it '
                                      'before this height was not looked at')
            out = _shielded.scan_blocks(self.chain, fvk, int(from_height),
                                        to_height, orchard_fvk=ofvk)
            out['wallet'] = name
            out['tip'] = tip
            if started_at:
                out['scanned_from'] = started_at
            return out
        except (_wallet.WalletError, _shielded.ShieldedError,
                _chain.ChainError, ValueError) as e:
            return _err(e)

    def shielded_balance(self, name: str, password: str, from_height: int = None,
                         blocks: int = None) -> dict:
        """Shielded value found for this wallet, without the note detail."""
        out = self.shielded_scan(name, password, from_height=from_height,
                                 blocks=blocks)
        if 'error' in out:
            return out
        notes = out.pop('notes', [])
        out['incoming_notes'] = sum(1 for n in notes if n['direction'] == 'incoming')
        out['outgoing_notes'] = sum(1 for n in notes if n['direction'] == 'outgoing')
        return out

    def shielded_scan_tx(self, txid: str, name: str = None, password: str = None,
                         viewing_key: str = None, account: int = None) -> dict:
        """Read one transaction's shielded outputs and actions with a key.

        Takes either a wallet (name + password), a `zxviews...` extended full
        viewing key, or a ZIP-316 `uview1...` unified key (which carries both
        pools at once). Notes that belong to the key come back decrypted,
        value and memo included; the rest stay encrypted, as they should.

        One transaction is cheap enough that both pools are always read: the
        raw bytes are fetched whenever the transaction could hold an Orchard
        bundle, because the explorer's row never carries one.
        """
        try:
            if viewing_key:
                keys = _shielded.keys_from_viewing_key(viewing_key)
                fvk, ofvk = keys['fvk'], keys.get('orchard_fvk')
            elif name:
                fvk = _wallet.shielded_key(name, password, account).fvk()
                ofvk = _wallet.orchard_key(name, password, account).fvk()
            else:
                raise ValueError("pass a wallet name (with password), a "
                                 "viewing_key, or a unified uview key")
            row = self.chain.transaction_row(txid)
            pools = ['sapling'] if fvk is not None else []
            if ofvk is not None and _shielded._may_hold_orchard(row):
                pools.append('orchard')
                raw = self.chain.raw_transaction(txid)
                scan = _shielded.scan_raw_transaction(
                    raw, fvk, txid, row.get('block_id'), orchard_fvk=ofvk)
            elif row.get('shielded_output_raw') or row.get('shielded_input_raw'):
                scan = _shielded.scan_explorer_row(dict(row, hash=txid), fvk)
            else:
                raw = self.chain.raw_transaction(txid)
                scan = _shielded.scan_raw_transaction(
                    raw, fvk, txid, row.get('block_id'))
            summary = _shielded.summarize([scan], pools=pools or ['sapling'])
            scan['found'] = summary['note_count']
            scan['received_zec'] = summary['received_zec']
            scan['pools_scanned'] = summary['pools_scanned']
            scan['pools_not_scanned'] = summary['pools_not_scanned']
            return scan
        except (_wallet.WalletError, _shielded.ShieldedError, _chain.ChainError,
                _bundles.UnknownLayout, ValueError) as e:
            return _err(e)

    def shielded_send(self, name: str, password: str, to: str, amount: float,
                      memo: str = None, broadcast: bool = False,
                      from_address: str = None, account: int = None) -> dict:
        """Send shielded ZEC -- for real, with a local zk-SNARK prover.

        A shielded spend carries a Groth16 proof (Sapling) or a Halo 2 one
        (Orchard), which is why the rest of this module -- pure Python -- can
        receive and read shielded value but not move it. The proving is done
        by a local light client (see `zcash/lightclient.py`): it syncs compact
        blocks from a lightwalletd server, keeps the note commitment trees,
        builds the proof and broadcasts. Same seed, same account, same notes.

        Two things have to be true before this can spend, and the response
        says which one is missing:

          * the prover is built (`shielded_backend_install`, once per host);
          * this wallet's light client has finished scanning
            (`shielded_sync_start`, then `shielded_sync_status`).

        Dry run unless broadcast=True. The dry run is real work: it checks the
        address, the balance and the sync state, and reports the fee band --
        it just stops before the proof, because a proof that is not broadcast
        is a proof thrown away.

        A configured node (ZCASH_RPC_URL) is still honoured, and still takes
        precedence when it holds the key: see `shielded_node_import`.
        """
        try:
            info = _keys.decode_address(to)
            if info['type'] not in ('sapling', 'unified'):
                return {'error': f"{to} is a {info['type']} address; use send() "
                                 f"for transparent payments"}
            if amount is None or float(amount) <= 0:
                return {'error': 'amount must be positive'}
            amount = float(amount)

            # A node that already holds the key proves faster than a fresh
            # scan, so it keeps precedence where the operator set one up.
            if self.chain.has_node:
                source = from_address
                if not source:
                    source = _wallet.shielded(name)['addresses'][0]['address']
                return _shielded.node_send(self.chain, source, to, amount, memo,
                                           broadcast=broadcast)

            backend = _lightclient.available()
            if not backend['installed']:
                keys_hint = self.shielded_export(name, password, account)
                return {
                    'error': 'no proving backend installed',
                    'how_to_send': [
                        'Run shielded_backend_install once on this host: it '
                        'builds a local Zcash light client that can prove and '
                        'broadcast a shielded spend, with no full node.',
                        'Or set ZCASH_RPC_URL to a zcashd/zebrad node and run '
                        'shielded_node_import.',
                        'Or import the extended spending key below into Zashi, '
                        'Ywallet, zingo or zcashd.',
                    ],
                    'backend': backend,
                    'extended_spending_key':
                        keys_hint.get('extended_spending_key'),
                    'to': to, 'amount_zec': amount,
                }

            if not _lightclient.initialized(name):
                return {
                    'error': f'the light client for {name!r} has not been set '
                             f'up yet',
                    'how_to_send': ['Call shielded_sync_start(name, password) '
                                    'and wait for shielded_sync_status to '
                                    'report synced.'],
                    'to': to, 'amount_zec': amount,
                }

            state = _lightclient.status(name)
            if state.get('syncing'):
                return {
                    'error': 'the light client is still scanning; a spend '
                             'needs the note commitment tree up to the tip',
                    'sync': state, 'to': to, 'amount_zec': amount,
                }
            if state.get('synced') is False:
                return {
                    'error': 'the light client is behind the chain tip',
                    'how_to_send': ['Call shielded_sync_start(name) and wait '
                                    'for shielded_sync_status to report '
                                    'synced.'],
                    'sync': state, 'to': to, 'amount_zec': amount,
                }

            zatoshis = int(round(amount * ZAT))
            bal = _lightclient.balance(name)
            spendable = (bal['sapling_spendable_zat']
                         + bal['orchard_spendable_zat'])
            if spendable <= zatoshis:
                return {
                    'error': f'not enough spendable shielded value: '
                             f'{spendable / ZAT:.8f} ZEC available, '
                             f'{amount:.8f} ZEC requested plus fee',
                    'balance': bal, 'to': to, 'amount_zec': amount,
                }

            if not broadcast:
                return {
                    'mode': 'DRY RUN', 'sent': False,
                    'to': to, 'to_type': info['type'],
                    'amount_zec': amount, 'amount_zat': zatoshis,
                    'memo': memo,
                    'spendable_zec': spendable / ZAT,
                    'fee_zec': _tx.MARGINAL_FEE * 2 / ZAT,
                    'fee_note': 'ZIP-317; the exact fee depends on how many '
                                'notes the light client selects',
                    'prover': backend,
                    'chain_tip_height': state.get('chain_tip_height'),
                    'note': 'nothing was proved or broadcast. Pass '
                            'broadcast=True to spend for real.',
                }

            if not password:
                return {'error': 'a password is required to spend: it unseals '
                                 'the light client identity'}
            result = _lightclient.send(name, password, to, zatoshis, memo)
            result['balance_before'] = bal
            result['resync'] = ('call shielded_sync_start to pick up the '
                                'change note')
            return result
        except (_wallet.WalletError, _shielded.ShieldedError, _chain.ChainError,
                _lightclient.LightClientError, ValueError) as e:
            return _err(e)

    def shielded_backend(self) -> dict:
        """Is there a local zk-SNARK prover on this host, and where."""
        out = _lightclient.available()
        out['node'] = self.chain.node_info()
        out['can_spend_shielded'] = bool(out['installed']) or self.chain.has_node
        return out

    def shielded_backend_install(self, force: bool = False,
                                 timeout: int = 3600) -> dict:
        """Build the local prover, once. Takes a while; it is a light client.

        This compiles zcash-devtool (zcash_client_backend + zcash_proofs) into
        ~/.mod/zcash/bin/. It needs cargo on the host and about ten minutes of
        CPU the first time; afterwards `shielded_send` just works.
        """
        if _lightclient.binary() and not force:
            return dict(_lightclient.available(), rebuilt=False)
        script = os.path.join(_HERE, '..', 'install_prover.sh')
        env = dict(os.environ)
        if force:
            env['ZCASH_PROVER_FORCE'] = '1'
        started = time.time()
        try:
            cp = subprocess.run(['bash', os.path.abspath(script)],
                                capture_output=True, text=True,
                                timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {'error': f'the build did not finish within {timeout}s; '
                             f'run `bash install_prover.sh` in a terminal'}
        if cp.returncode != 0:
            tail = (cp.stderr or cp.stdout or '').strip().splitlines()[-8:]
            return {'error': 'the prover build failed', 'output': tail}
        return dict(_lightclient.available(), rebuilt=True,
                    seconds=round(time.time() - started, 1))

    def shielded_sync_start(self, name: str, password: str = None,
                            birthday: int = None) -> dict:
        """Set up this wallet's light client if needed, and start scanning.

        The first call restores the wallet's own mnemonic into a light client
        (which is why it wants the password) and starts a background scan from
        the wallet's birthday. Later calls just start a scan. Poll
        `shielded_sync_status` -- this returns immediately either way.
        """
        try:
            if not _lightclient.binary():
                return {'error': 'no proving backend installed',
                        'how_to_fix': 'run shielded_backend_install once',
                        'backend': _lightclient.available()}
            created = None
            if not _lightclient.initialized(name):
                if not password:
                    return {'error': 'a password is required the first time: '
                                     'the light client is restored from this '
                                     "wallet's mnemonic"}
                secret = _wallet.reveal(name, password)
                if not secret.get('mnemonic'):
                    return {'error': f'wallet {name!r} holds imported keys '
                                     f'only; a shielded account needs a seed'}
                if birthday is None:
                    account = (_wallet.info(name) or {}).get('shielded') or {}
                    birthday = account.get('birthday') or \
                        (_wallet.shielded(name) or {}).get('birthday')
                created = _lightclient.init(name, secret['mnemonic'], password,
                                            birthday)
            job = _lightclient.sync_start(name)
            out = _lightclient.status(name)
            out['sync_job'] = job
            if created:
                out['created'] = created
                out['note'] = ('the light client was restored from this '
                               "wallet's seed and is scanning from block "
                               f"{created.get('birthday')}. A wallet with an "
                               'old birthday can take a while.')
            return out
        except (_wallet.WalletError, _lightclient.LightClientError,
                ValueError) as e:
            return _err(e)

    def shielded_sync_status(self, name: str) -> dict:
        """How far this wallet's light client has scanned, and can it spend."""
        try:
            out = _lightclient.status(name)
            if out.get('initialized') and not out.get('syncing'):
                try:
                    out['balance'] = _lightclient.balance(name)
                except _lightclient.LightClientError:
                    pass                # a wallet with no summary yet
            return out
        except (_lightclient.LightClientError, ValueError) as e:
            return _err(e)

    def shielded_sync_stop(self, name: str) -> dict:
        """Stop a running background scan."""
        try:
            return _lightclient.sync_stop(name)
        except (_lightclient.LightClientError, ValueError) as e:
            return _err(e)

    def shielded_spendable(self, name: str) -> dict:
        """Shielded balance as the prover sees it -- what can actually be sent.

        `shielded_balance` scans the explorer and reports what *arrived*; this
        reports what the light client holds a spendable note for, which is the
        number a send is checked against.
        """
        try:
            if not _lightclient.initialized(name):
                return {'error': f'no light client for {name!r}',
                        'how_to_fix': 'call shielded_sync_start first'}
            out = _lightclient.balance(name)
            out['sync'] = {k: v for k, v in _lightclient.status(name).items()
                           if k in ('syncing', 'percent', 'synced',
                                    'blocks_remaining', 'max_scanned_height')}
            return out
        except (_lightclient.LightClientError, ValueError) as e:
            return _err(e)

    def shielded_shield(self, name: str, password: str,
                        broadcast: bool = False) -> dict:
        """Move this wallet's transparent balance into the shielded pool.

        Shielding is itself a shielded output, so it needs the same prover a
        send does. Dry run unless broadcast=True.
        """
        try:
            if not _lightclient.initialized(name):
                return {'error': f'no light client for {name!r}',
                        'how_to_fix': 'call shielded_sync_start first'}
            bal = _lightclient.balance(name)
            if not broadcast:
                return {'mode': 'DRY RUN', 'shielded': False,
                        'transparent_spendable_zec':
                            bal['transparent_spendable_zat'] / ZAT,
                        'note': 'pass broadcast=True to shield for real'}
            if bal['transparent_spendable_zat'] <= 0:
                return {'error': 'no transparent balance to shield',
                        'balance': bal}
            out = _lightclient.shield(name, password)
            out['balance_before'] = bal
            return out
        except (_lightclient.LightClientError, _wallet.WalletError,
                ValueError) as e:
            return _err(e)

    def shielded_node_import(self, name: str, password: str,
                             rescan: str = "whenkeyisnew",
                             account: int = None) -> dict:
        """Hand this wallet's Sapling spending key to the configured node."""
        try:
            xsk = _wallet.shielded_key(name, password, account)
            birthday = (_wallet.shielded(name) or {}).get('birthday')
            return _shielded.node_import_key(self.chain, xsk.encode(), rescan,
                                             birthday)
        except (_wallet.WalletError, _shielded.ShieldedError,
                _chain.ChainError, ValueError) as e:
            return _err(e)

    def shielded_operation(self, operation_id: str) -> dict:
        """Status of a node-side shielded send."""
        try:
            return _shielded.node_operation(self.chain, operation_id)
        except (_shielded.ShieldedError, _chain.ChainError) as e:
            return _err(e)

    def validate(self, addr: str) -> dict:
        """Check whether an address is a valid Zcash address, and its type."""
        try:
            info = _keys.decode_address(addr)
        except ValueError as e:
            return {'address': addr, 'valid': False, 'error': str(e)}
        out = {'address': addr, 'valid': True, 'type': info['type'],
               'spendable_by_this_module': info['spendable']}
        for key in ('receivers', 'paid_receiver', 'pool', 'transparent_address'):
            if info.get(key):
                out[key] = info[key]
        if info.get('reason') or info.get('note'):
            out['note'] = info.get('reason') or info.get('note')
        if info.get('script_pubkey'):
            out['script_pubkey'] = info['script_pubkey'].hex()
        return out

    def estimate_fee(self, inputs: int = 1, outputs: int = 2) -> dict:
        """ZIP-317 conventional fee for a transparent transaction."""
        fee = _tx.conventional_fee(inputs, outputs)
        return {'inputs': inputs, 'outputs': outputs, 'fee_zatoshi': fee,
                'fee_zec': _zec(fee), 'estimated_size_bytes': _tx.estimate_size(inputs, outputs),
                'rule': 'ZIP-317 conventional fee'}

    def send(self, name: str, password: str, to: str, amount: float = None,
             broadcast: bool = False, amount_zatoshi: int = None,
             from_address: str = None, fee_zatoshi: int = None) -> dict:
        """Send ZEC from a wallet to a transparent address.

        Dry run by default: the transaction is built and fully signed but not
        submitted. Pass broadcast=True to actually publish it.
        """
        try:
            if amount_zatoshi is None:
                if amount is None:
                    raise ValueError("specify amount (ZEC) or amount_zatoshi")
                amount_zatoshi = int(round(float(amount) * ZAT))
            amount_zatoshi = int(amount_zatoshi)
            if amount_zatoshi <= 0:
                raise ValueError("amount must be positive")

            dest = _keys.decode_address(to)
            if not dest['spendable'] and dest['type'] not in ('p2pkh', 'p2sh'):
                raise ValueError(
                    f"cannot send to a {dest['type']} address: {dest.get('reason')}. "
                    "This module spends and pays only transparent (t1/t3) addresses.")

            privkeys = _wallet.private_keys(name, password)
            entries = _wallet.addresses(name)
            sources = [e['address'] for e in entries]
            if from_address:
                if from_address not in sources:
                    raise ValueError(f"{from_address} is not in wallet {name!r}")
                sources = [from_address]

            utxos, seen = [], set()
            for addr in sources:
                for u in self.chain.utxos(addr):
                    key = (u['txid'], u['vout'])
                    if key in seen:
                        continue
                    seen.add(key)
                    utxos.append(dict(u, address=addr))
            if not utxos:
                raise ValueError(
                    f"no spendable outputs in wallet {name!r}"
                    + (f" at {from_address}" if from_address else "")
                    + " -- the balance may be unconfirmed or held in a shielded pool")

            branch_id = self.chain.consensus_branch_id()
            expiry = self.chain.tip_height() + _tx.DEFAULT_EXPIRY_DELTA
            change_address = from_address or sources[0]

            transaction, meta = _tx.build_transaction(
                utxos, [(to, amount_zatoshi)], change_address,
                branch_id, expiry, fee_zatoshi)

            for i, txin in enumerate(transaction.vin):
                owner = next(u['address'] for u in utxos
                             if u['txid'] == txin.txid and int(u['vout']) == txin.vout)
                priv = privkeys.get(owner)
                if priv is None:
                    raise ValueError(f"wallet holds no key for {owner}")
                transaction.sign_input(i, priv)
                if not transaction.verify_input(i):
                    raise ValueError(f"signature check failed for input {i}")

            raw = transaction.hex()
            result = {
                'wallet': name, 'to': to,
                # A unified address is paid through whichever receiver the
                # sender supports. Ours is the transparent one, and the
                # recipient should not have to guess that from the tx.
                **({'paid_receiver': 'p2pkh (transparent)',
                    'privacy_note': dest.get('note'),
                    'paid_transparent_address': dest.get('transparent_address')}
                   if dest['type'] == 'unified' else {}),
                'amount_zec': _zec(amount_zatoshi), 'amount_zatoshi': amount_zatoshi,
                'fee_zec': _zec(meta['fee_zatoshi']), 'fee_zatoshi': meta['fee_zatoshi'],
                'change_zec': _zec(meta['change_zatoshi']),
                'change_address': change_address if meta['change_zatoshi'] else None,
                'inputs': meta['inputs'], 'outputs': meta['outputs'],
                'size_bytes': len(raw) // 2,
                'expiry_height': expiry,
                'consensus_branch_id': f"{branch_id:08x}",
                'txid': transaction.txid(),
                'raw_transaction': raw,
                'signatures_verified': True,
            }
            if not broadcast:
                result['broadcast'] = False
                result['mode'] = 'DRY RUN'
                result['note'] = (
                    "DRY RUN - nothing was submitted to the network and no funds "
                    "moved. The transaction above is fully signed and valid. "
                    "Re-run with broadcast=True to publish it, or submit "
                    "raw_transaction yourself with broadcast_raw.")
                return result

            pushed = self.chain.broadcast(raw)
            result['broadcast'] = True
            result['mode'] = 'BROADCAST'
            result['txid'] = pushed['txid']
            result['broadcast_via'] = pushed['via']
            result['explorer'] = f"https://blockchair.com/zcash/transaction/{pushed['txid']}"
            return result
        except (ValueError, _wallet.WalletError, _chain.ChainError) as e:
            return _err(e)

    def broadcast_raw(self, raw_transaction: str) -> dict:
        """Submit an already-signed raw transaction hex to the network."""
        try:
            pushed = self.chain.broadcast(raw_transaction.strip())
            return {'txid': pushed['txid'], 'via': pushed['via'], 'broadcast': True,
                    'explorer': f"https://blockchair.com/zcash/transaction/{pushed['txid']}"}
        except _chain.ChainError as e:
            return _err(e)

    # ── Bridge ─────────────────────────────────────────────────────────────

    def bridge_chains(self) -> dict:
        """Chains and assets ZEC can be bridged to or from."""
        try:
            chains = _bridge.chains()
            return {'chain_count': len(chains),
                    'asset_count': sum(len(c['assets']) for c in chains),
                    'routes': ['near-intents', 'maya'], 'chains': chains}
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_quote(self, to_asset: str, amount: float, recipient: str,
                     refund_to: str, from_asset: str = "ZEC") -> dict:
        """Price a bridge without reserving anything.

        `to_asset` is 'ETH', 'eth:USDC', 'BTC', 'base:ETH', ... Set
        from_asset to bridge *into* ZEC (then recipient is your t-address).
        """
        try:
            return _bridge.quote(from_asset, to_asset, amount, recipient, refund_to, dry=True)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_start(self, to_asset: str, amount: float, recipient: str,
                     refund_to: str, from_asset: str = "ZEC",
                     slippage_bps: int = 100) -> dict:
        """Reserve a real deposit address for a bridge.

        Nothing moves until you fund the returned deposit address. When the
        origin is ZEC you can do that with bridge_send or send().
        """
        try:
            return _bridge.quote(from_asset, to_asset, amount, recipient,
                                 refund_to, dry=False, slippage_bps=slippage_bps)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_status(self, deposit_address: str) -> dict:
        """Track a bridge by its deposit address."""
        try:
            return _bridge.status(deposit_address)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_payment(self, from_asset: str, amount: float,
                       deposit_address: str) -> dict:
        """The exact EVM transaction that funds a bridge deposit address.

        Bridging *into* ZEC is funded on the origin chain, and this module
        holds no keys there -- so instead of a signature it returns the
        transaction itself: chain id, `to`, `value` and ERC-20 calldata, with
        the amount converted by the same path that priced the quote. A browser
        wallet (MetaMask and anything else speaking EIP-1193) can sign it
        as-is; nothing here ever sees a private key from that chain.
        """
        try:
            return _bridge.payment(from_asset, amount, deposit_address)
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_networks(self) -> dict:
        """EVM networks a browser wallet can be pointed at to fund a deposit."""
        nets = [{'chain': c, 'chain_id': v[0], 'chain_id_hex': hex(v[0]),
                 'name': v[1], 'native_symbol': v[2], 'rpc': v[3],
                 'explorer': v[4]}
                for c, v in sorted(_bridge.EVM_NETWORKS.items(),
                                   key=lambda kv: kv[1][0])]
        unverified = sorted(_bridge.EVM_CHAINS - set(_bridge.EVM_NETWORKS))
        return {
            'count': len(nets), 'networks': nets,
            'evm_without_verified_chain_id': unverified,
            'note': 'bridge_payment builds a signable transaction for these '
                    'chains. Other EVM chains the router reaches are listed '
                    'under evm_without_verified_chain_id -- pay those from a '
                    'wallet already pointed at that network.',
        }

    def bridge_maya(self, to_asset: str = None, amount: float = None,
                    destination: str = None) -> dict:
        """Maya Protocol route health, or a ZEC quote when given all three args."""
        try:
            if to_asset and amount and destination:
                return _bridge.maya_quote(to_asset, amount, destination)
            return _bridge.maya_status()
        except _bridge.BridgeError as e:
            return _err(e)

    def bridge_send(self, name: str, password: str, to_asset: str, amount: float,
                    recipient: str, broadcast: bool = False,
                    slippage_bps: int = 100, refund_to: str = None) -> dict:
        """Bridge ZEC out of this wallet in one step.

        Reserves a deposit address, then pays it from the wallet. Dry run
        unless broadcast=True; the reservation is only made for real when
        broadcasting, so dry runs cost nothing.
        """
        try:
            entries = _wallet.addresses(name)
            if not entries:
                raise ValueError(f"wallet {name!r} has no addresses")
            refund = refund_to or entries[0]['address']
            if not _keys.is_valid_address(refund):
                raise ValueError(f"refund address {refund} is not a valid Zcash address")

            quote = _bridge.quote("ZEC", to_asset, amount, recipient, refund,
                                  dry=not broadcast, slippage_bps=slippage_bps)
            out = {'bridge': quote, 'wallet': name}

            if not broadcast:
                out['mode'] = 'DRY RUN'
                out['broadcast'] = False
                out['note'] = (
                    "DRY RUN - no deposit address was reserved and no ZEC was sent. "
                    f"Quoted {quote['amount_in']} ZEC -> {quote['amount_out']} "
                    f"{quote['to']}. Re-run with broadcast=True to reserve a "
                    "deposit address and pay it from this wallet.")
                # Show what the funding transaction would look like.
                out['funding_preview'] = self.send(
                    name, password, refund, amount=amount, broadcast=False)
                return out

            deposit = quote['deposit_address']
            payment = self.send(name, password, deposit, amount=amount, broadcast=True)
            out['payment'] = payment
            if 'error' in payment:
                out['mode'] = 'FAILED'
                out['broadcast'] = False
                out['note'] = (
                    f"Deposit address {deposit} was reserved but funding it failed: "
                    f"{payment['error']}. Nothing was sent; you can pay "
                    f"{deposit} manually before {quote['deadline']}.")
                return out
            out['mode'] = 'BROADCAST'
            out['broadcast'] = True
            out['track_with'] = {'fn': 'bridge_status', 'deposit_address': deposit}
            out['note'] = (
                f"Sent {amount} ZEC (txid {payment['txid']}) to {deposit}. "
                f"Expect ~{quote['amount_out']} {quote['to']} at {recipient} "
                f"in about {quote['eta_seconds']}s.")
            return out
        except (ValueError, _wallet.WalletError, _bridge.BridgeError, _chain.ChainError) as e:
            return _err(e)

    # ── Shielded bridging ──────────────────────────────────────────────────
    #
    # The two directions are genuinely different problems, so they are two
    # functions rather than one with a flag.
    #
    # IN is solved. The router accepts a ZIP-316 unified address as the ZEC
    # recipient, so a shielded-only unified address forces the solver to pay
    # into the pool. Nothing is proven on our side -- the solver creates the
    # shielded output -- which is why this works at all in a module that
    # cannot create one itself.
    #
    # OUT is not, and cannot be made so here: the deposit address is a
    # t-address and paying it from the pool is a Sapling spend. With a node
    # configured we drive it; without one we still reserve the deposit
    # address and hand back the exact payment a proving wallet must make,
    # because a reservation the user can complete by hand is worth more than
    # a refusal.

    def _readable_pools(self) -> set:
        """Shielded pools this module can actually decrypt notes in.

        Read from capabilities() rather than hardcoded, so that the day
        Orchard reading lands, bridging into an Orchard receiver starts
        working without anyone remembering to edit the bridge -- and until
        that day, an address advertising one is trimmed rather than trusted.
        """
        caps = self.capabilities()
        pools = set()
        for pool in ('sapling', 'orchard'):
            row = caps.get(f'shielded_{pool}') or {}
            if row.get('read') or row.get('receive'):
                pools.add(pool)
        return pools

    def bridge_shielded_plan(self) -> dict:
        """Which shielded bridge directions work here right now, and why."""
        backend = self.shielded_backend()
        plan = _bridge.shielded_plan(bool(backend.get('can_spend_shielded')))
        plan['node'] = backend.get('node')
        plan['prover'] = {k: backend.get(k) for k in
                          ('installed', 'binary', 'can_spend_shielded')}
        plan['readable_pools'] = sorted(self._readable_pools())
        plan['privacy'] = {
            'in': _bridge.privacy('in', 'origin chain'),
            'out': _bridge.privacy('out', 'destination chain'),
        }
        return plan

    def bridge_shielded_address(self, address: str = None,
                                name: str = None) -> dict:
        """The address a bridge should be given to pay you *shielded*.

        Give it an address or a wallet name. A unified address that also
        carries a transparent receiver comes back re-encoded without it,
        because a solver offered both will pick the transparent one.
        """
        try:
            target = address
            if not target:
                if not name:
                    raise ValueError('give an address or a wallet name')
                account = _wallet.shielded(name)
                if not account or not account.get('addresses'):
                    raise ValueError(
                        f"wallet {name!r} has no shielded account; add one with "
                        "shielded_upgrade")
                target = account['addresses'][0]['unified_address']
            out = _bridge.shielded_recipient(target,
                                             readable=self._readable_pools())
            if name:
                out['wallet'] = name
            return out
        except (ValueError, _wallet.WalletError, _bridge.BridgeError) as e:
            return _err(e)

    def bridge_shielded_in(self, from_asset: str, amount: float,
                           refund_to: str, recipient: str = None,
                           name: str = None, reserve: bool = False,
                           slippage_bps: int = 100,
                           via_transparent: str = None,
                           accept_public_leg: bool = False) -> dict:
        """Bridge another chain's asset straight into your shielded pool.

        `recipient` is your own zs1 or u1 address (or pass `name` to use a
        wallet's). `refund_to` is your address on the ORIGIN chain -- a refund
        is paid there, and no solver can refund into the shielded pool.

        Quotes only, unless reserve=True: reserving returns a real deposit
        address you fund yourself from the origin chain.

        Whether the router pays a z-address at all is the router's decision,
        not this module's, and it has changed. When it refuses, this falls
        back to the two-leg route -- bridge to a transparent address you own
        (`via_transparent`, or the wallet's first one), then shield it with
        shielded_shield -- and says plainly that the first leg is public.

        The fallback only ever quotes. Reserving a deposit address on it takes
        `accept_public_leg=True` as well, because someone who asked to reserve
        a *shielded* bridge has not agreed to fund a public one.
        """
        try:
            target = recipient
            if not target:
                if not name:
                    raise ValueError(
                        'give recipient (your zs1/u1 address) or name (a wallet)')
                account = _wallet.shielded(name)
                if not account or not account.get('addresses'):
                    raise ValueError(
                        f"wallet {name!r} has no shielded account; add one with "
                        "shielded_upgrade")
                target = account['addresses'][0]['unified_address']

            try:
                quote = _bridge.shielded_quote(from_asset, amount, target,
                                               refund_to, dry=not reserve,
                                               slippage_bps=slippage_bps,
                                               readable=self._readable_pools())
            except _bridge.ShieldedRouteUnavailable as e:
                return self._shield_in_two_legs(
                    e, from_asset, amount, refund_to, target, name,
                    via_transparent, reserve and accept_public_leg,
                    slippage_bps, asked_to_reserve=reserve)
            if name:
                quote['wallet'] = name
            if not reserve:
                quote['mode'] = 'QUOTE'
                quote['note'] = (
                    f"QUOTE ONLY - nothing reserved and nothing sent. "
                    f"{quote['amount_in']} {quote['from']} would arrive as "
                    f"~{quote['amount_out']} ZEC in your shielded pool. Re-run "
                    "with reserve=True to get a deposit address.")
                return quote
            quote['mode'] = 'RESERVED'
            quote['fund_from'] = quote['from']
            quote['note'] = (
                f"Deposit address reserved on {quote['from'].split(':')[0]}. "
                f"Send exactly {quote['amount_in']} {quote['from']} to "
                f"{quote['deposit_address']} before {quote['deadline']}. This "
                "module cannot pay it for you -- it is on another chain. The "
                f"~{quote['amount_out']} ZEC lands as a shielded note; find it "
                "with shielded_scan once it arrives.")
            quote['track_with'] = {'fn': 'bridge_status',
                                   'deposit_address': quote['deposit_address']}
            return quote
        except (ValueError, _wallet.WalletError, _bridge.BridgeError) as e:
            return _err(e)

    def _shield_in_two_legs(self, why, from_asset, amount, refund_to, target,
                            name, via_transparent, reserve, slippage_bps,
                            asked_to_reserve=False):
        """The router refused the z-address. Bridge public, then shield.

        This is a worse route and is returned saying so: the ZEC lands in the
        clear and is only private after the second leg. It is still the honest
        answer, because the alternative -- handing back "recipient is not
        valid" -- reads as "your address is wrong" when the address is fine.

        `reserve` here has already been ANDed with the caller's explicit
        consent to the public leg; `asked_to_reserve` is what they originally
        wanted, and is what the response uses to tell them the reservation did
        not happen.
        """
        t_addr = via_transparent
        if not t_addr and name:
            entries = _wallet.addresses(name)
            if entries:
                t_addr = entries[0]['address']
        if not t_addr:
            return {
                'error': str(why),
                'route': 'unavailable',
                'shielded': False,
                'how_to_fix': (
                    'Pass via_transparent (a t-address you control) or name (a '
                    'wallet with one) and this returns the two-leg route: '
                    'bridge to that t-address, then shielded_shield moves it '
                    'into the pool.'),
            }
        quote = _bridge.quote(from_asset, _bridge.ZEC_ASSET, amount, t_addr,
                              refund_to, dry=not reserve,
                              slippage_bps=slippage_bps)
        quote.update({
            'route_kind': 'transparent-then-shield',
            'shielded': False,
            'shielded_direct_unavailable': str(why),
            'recipient_given': target.get('given') if isinstance(target, dict)
                               else target,
            'destination_pool': 'transparent (leg 1) -> sapling (leg 2)',
            'mode': 'RESERVED' if reserve else 'QUOTE',
            'legs': [
                {'leg': 1, 'what': f"{from_asset} -> ZEC at {t_addr}",
                 'private': False,
                 'why': 'the solver pays a transparent address, so this much '
                        'ZEC is visible on the Zcash chain'},
                {'leg': 2, 'what': 'shielded_shield moves it into the pool',
                 'private': True, 'fn': 'shielded_shield',
                 'why': 'a shielding transaction is a shielded output; after '
                        'it, the balance is encrypted -- but the amount that '
                        'entered the pool, and when, stays public'},
            ],
            'shield_with': {'fn': 'shielded_shield',
                            'args': {'name': name, 'broadcast': True}}
                           if name else
                           {'fn': 'shielded_shield',
                            'note': 'needs a wallet in this module that owns '
                                    f'{t_addr}'},
        })
        if asked_to_reserve and not reserve:
            quote['not_reserved'] = (
                'You asked to reserve a shielded bridge and this is not one. '
                'Nothing was reserved. Re-run with accept_public_leg=True to '
                'reserve a deposit address whose ZEC lands in the clear at '
                f'{t_addr}, or wait for the router to take z-addresses again.')
            quote['reserve_with'] = {
                'fn': 'bridge_shielded_in',
                'args': {'from_asset': from_asset, 'amount': amount,
                         'refund_to': refund_to, 'via_transparent': t_addr,
                         'reserve': True, 'accept_public_leg': True},
            }
        quote['note'] = (
            ('Deposit address reserved. ' if reserve else 'QUOTE ONLY - '
             'nothing reserved and nothing sent. ') +
            f"The direct shielded route is refused by the router right now, so "
            f"this bridges to your transparent address {t_addr} first. That leg "
            f"is public: ~{quote.get('amount_out')} ZEC will be visible there "
            f"until you shield it.")
        if reserve and quote.get('deposit_address'):
            quote['track_with'] = {'fn': 'bridge_status',
                                   'deposit_address': quote['deposit_address']}
        return quote

    def bridge_shielded_out(self, name: str, password: str, to_asset: str,
                            amount: float, recipient: str,
                            broadcast: bool = False, refund_to: str = None,
                            slippage_bps: int = 100,
                            from_address: str = None) -> dict:
        """Bridge shielded ZEC out to another chain.

        Reserves the deposit address, then spends a shielded note into it --
        which needs a proving node. Without one this stops at the reservation
        and hands you the exact payment to make from a proving wallet.

        Dry run unless broadcast=True. Read `privacy` before using it: leaving
        the shielded pool is public, and no bridge can change that.
        """
        try:
            account = _wallet.shielded(name)
            if not account or not account.get('addresses'):
                raise ValueError(
                    f"wallet {name!r} has no shielded account; add one with "
                    "shielded_upgrade")
            source = from_address or account['addresses'][0]['address']

            # The refund lands on the Zcash side and a solver cannot pay into
            # the pool, so it has to be one of this wallet's t-addresses.
            refund = refund_to
            if not refund:
                entries = _wallet.addresses(name)
                if not entries:
                    raise ValueError(
                        f"wallet {name!r} has no transparent address to refund "
                        "to; pass refund_to")
                refund = entries[0]['address']

            quote = _bridge.shielded_out_quote(
                to_asset, amount, recipient, refund,
                dry=not broadcast, slippage_bps=slippage_bps)
            # Which prover exists is shielded_send's decision, not this
            # function's -- it already prefers a configured node over the
            # local light client and knows when the client is still scanning.
            # Asking chain.has_node here instead would have said "no prover"
            # on a host that had built one.
            backend = self.shielded_backend()
            can_prove = bool(backend.get('can_spend_shielded'))
            out = {'bridge': quote, 'wallet': name, 'from_shielded': source,
                   'privacy': quote['privacy'],
                   'proving': ('node' if self.chain.has_node
                               else 'local light client' if can_prove
                               else 'none — you complete the spend yourself')}

            if not broadcast:
                out['mode'] = 'DRY RUN'
                out['broadcast'] = False
                out['note'] = (
                    "DRY RUN - nothing reserved, nothing spent. Quoted "
                    f"{quote['amount_in']} ZEC from your shielded notes -> "
                    f"~{quote['amount_out']} {quote['to']}. Re-run with "
                    "broadcast=True to reserve a deposit address"
                    + (f" and have the {out['proving']} prove and send the "
                       "spend." if can_prove else
                       " and get the exact payment to make from a proving "
                       "wallet."))
                return out

            deposit = quote['deposit_address']
            if not can_prove:
                # Reserved, unpaid, and completable by hand -- say exactly
                # that rather than pretending it failed or that it worked.
                out['mode'] = 'RESERVED — PAY IT YOURSELF'
                out['broadcast'] = False
                out['manual_payment'] = {
                    'from': source,
                    'to': deposit,
                    'amount_zec': quote['amount_in'],
                    'before': quote['deadline'],
                    'memo': None,
                }
                out['how'] = [
                    f"Export this account's spending key: shielded_export("
                    f"name={name!r}, password=...).",
                    "Import it into a wallet that can prove a Sapling spend — "
                    "Zashi, Ywallet or zingo.",
                    f"From that wallet send exactly {quote['amount_in']} ZEC "
                    f"from {source} to {deposit} before {quote['deadline']}.",
                    f"Track it here with bridge_status("
                    f"deposit_address={deposit!r}).",
                    "Or build the local prover once with "
                    "shielded_backend_install() (or set ZCASH_RPC_URL to a "
                    "node holding the key) and re-run this — it will do all "
                    "of that in one step.",
                ]
                out['note'] = (
                    f"The deposit address {deposit} is reserved and the swap is "
                    f"live until {quote['deadline']}, but NO ZEC WAS SENT: "
                    "spending a shielded note needs a zk-SNARK proof, and this "
                    "host has no prover built. Pay it from a proving wallet — "
                    "or run shielded_backend_install() — and the swap "
                    "completes normally.")
                return out

            payment = self.shielded_send(
                name, password, deposit, float(quote['amount_in']),
                broadcast=True, from_address=source)
            if isinstance(payment, dict) and payment.get('error'):
                out['payment'] = payment
                out['mode'] = 'RESERVED — SPEND FAILED'
                out['broadcast'] = False
                out['note'] = (
                    f"Deposit address {deposit} was reserved but the shielded "
                    f"spend failed: {payment['error']}. No ZEC left the pool. "
                    f"The swap is still live until {quote['deadline']} — pay "
                    f"{quote['amount_in']} ZEC to {deposit} by any means and "
                    "it completes.")
                return out
            out['payment'] = payment
            out['mode'] = 'BROADCAST'
            out['broadcast'] = True
            out['track_with'] = {'fn': 'bridge_status',
                                 'deposit_address': deposit}
            out['note'] = (
                f"Proving and sending {quote['amount_in']} ZEC from {source} "
                f"to {deposit} via the {out['proving']}. "
                + (f"Watch the spend with shielded_operation("
                   f"{payment['operation_id']!r}). "
                   if payment.get('operation_id') else
                   f"Spend txid {payment.get('txid')}. ")
                + f"Watch the swap with bridge_status({deposit!r}). Expect "
                f"~{quote['amount_out']} {quote['to']} at {recipient}.")
            return out
        except (ValueError, _wallet.WalletError, _bridge.BridgeError,
                _shielded.ShieldedError, _chain.ChainError) as e:
            return _err(e)

    # ── Learning and the agent ─────────────────────────────────────────────

    def learn(self, topic: str = None, level: str = None, path: str = None,
              glossary: bool = False) -> dict:
        """Plain-language Zcash lessons for someone starting from zero.

        No arguments lists them. `topic` opens one in full. `path` follows a
        reading order ('beginner', 'sending', 'privacy', 'bridging',
        'developer'). glossary=True returns every defined term.
        """
        try:
            if glossary:
                return _learn.glossary()
            if topic:
                return _learn.lesson(topic)
            return _learn.lessons(level=level, path=path)
        except KeyError as e:
            return {'error': str(e).strip("'")}

    def explain(self, term: str) -> dict:
        """Define one Zcash word, with the lesson that puts it in context."""
        try:
            return _learn.explain(term)
        except KeyError as e:
            return {'error': str(e).strip("'")}

    def ask(self, question: str, ground: bool = True) -> dict:
        """Ask about Zcash or about this module, in your own words.

        Answers from the written lessons, calls read-only functions to ground
        the answer in live data, and writes out the exact call you would make
        next. It never calls a function that spends, deletes or reveals a
        secret -- those come back as `actions` for you to run.
        """
        try:
            return _agent.ask(question, mod=self, ground=ground)
        except _agent.AgentError as e:
            return _err(e)

    def agent_status(self) -> dict:
        """What the answering agent is backed by right now."""
        return _agent.status()

    # ── Meta ───────────────────────────────────────────────────────────────

    def capabilities(self) -> dict:
        """What this module can and cannot do, and why."""
        node = self.chain.node_info()
        prover = _lightclient.available()
        can_spend = bool(prover['installed']) or self.chain.has_node
        spend_how = ('a local light client proves and broadcasts '
                     '(zcash_client_backend + zcash_proofs)'
                     if prover['installed'] else
                     'a configured node proves and broadcasts'
                     if self.chain.has_node else
                     'no proving backend yet -- run shielded_backend_install')
        return {
            'explorer': {'supported': True, 'source': 'blockchair'},
            'transparent_wallet': {
                'supported': True,
                'details': 'BIP39/BIP44 HD wallet (m/44\'/133\'/0\'/0/i), '
                           'AES-256-GCM encrypted at rest',
            },
            'send_transparent': {
                'supported': True,
                'details': 'NU5 v5 transactions, ZIP-244 signature digest, '
                           'ZIP-317 fees, verified against mainnet',
            },
            'shielded_sapling': {
                'receive': True,
                'read': True,
                'send': can_spend,
                'send_how': spend_how,
                'details': 'Real ZIP-32 keys (m/32\'/133\'/account\'), zs1 and '
                           'ZIP-316 unified addresses, note decryption with the '
                           'incoming and outgoing viewing keys, note '
                           'commitments and nullifiers -- all pure Python, '
                           'pinned to the official Zcash test vectors.',
                'cannot': None if can_spend else
                          'Create a shielded output or spend a note without a '
                          'proving backend: both need a Groth16 proof. Run '
                          'shielded_backend_install once to build the local '
                          'light client that does it, or set ZCASH_RPC_URL.',
                'spend_detection': 'nullifiers, from the light client\'s own '
                                   'commitment tree' if prover['installed']
                                   else 'nullifiers, node only'
                                   if self.chain.has_node
                                   else 'unavailable without a proving backend '
                                        '(note positions need the commitment '
                                        'tree)',
            },
            'shielded_orchard': {
                'receive': True,
                'read': True,
                'send': can_spend,
                'send_how': spend_how,
                'details': 'Real ZIP-32 Orchard keys from the same seed and the '
                           'same account path, unified addresses that advertise '
                           'an Orchard receiver, and action decryption with the '
                           'incoming and outgoing viewing keys -- Pallas, '
                           'Sinsemilla and Poseidon in pure Python, pinned to '
                           'the official Zcash test vectors.',
                'cannot': None if can_spend else
                          'Create an Orchard action without a proving backend: '
                          'that needs a Halo 2 proof. Run '
                          'shielded_backend_install once, or set ZCASH_RPC_URL.',
                'spend_detection': 'nullifiers, no node needed -- an Orchard '
                                   'nullifier does not depend on the note\'s '
                                   'position in the commitment tree, so a scan '
                                   'that covers the spending block sees it',
                'limits': 'The public explorer does not serialize Orchard '
                          'actions into its transaction rows, so a scan without '
                          'a node fetches each candidate transaction whole and '
                          f'covers {_shielded.ORCHARD_EXPLORER_MAX_BLOCKS} '
                          'blocks at a time instead of '
                          f'{_shielded.MAX_EXPLORER_BLOCKS}. Transactions in a '
                          'serialization this module cannot parse (the chain '
                          'tip now carries some) are counted and reported as '
                          'unreadable rather than passed off as empty.',
            },
            'bridge': {
                'supported': True,
                'routes': ['near-intents', 'maya'],
                'details': 'ZEC <-> 30+ chains including Ethereum, Base, '
                           'Arbitrum, Solana, BTC and Tron',
            },
            'node': node,
            'shielded_prover': prover,
            'safety': 'send(), shielded_send() and bridge_send() are dry runs '
                      'unless broadcast=True',
        }

    def mcp(self, message: dict = None, tool: str = None, args: dict = None,
            url: str = None) -> dict:
        """The module's MCP server, over the mod protocol.

        With no arguments: the whole schema -- every tool, its arguments, which
        ones need the token, and copy-paste client config. With `message`: one
        JSON-RPC 2.0 message, handled exactly as POST /mcp handles it. With
        `tool` (+ `args`): one tool called directly.

        The tools themselves are served over HTTP by api.py at POST /mcp and
        over stdio by `python3 mcp.py`; this is the same registry reached
        through the fleet's front door, which the gate has already
        owner-checked -- so a call arriving here is treated as local.
        """
        try:
            server = self._mcp_server()
        except Exception as e:
            return _err(e)
        if message is not None:
            if not isinstance(message, dict):
                return {'error': 'message must be a JSON-RPC 2.0 object'}
            response = server.handle(message, server.LOCAL_CTX)
            # A notification has no response; say so rather than return null.
            return response or {'accepted': True,
                                'note': 'notification -- no response is defined'}
        if tool:
            try:
                return {'tool': tool,
                        'result': server.call_tool(tool, args or {},
                                                   server.LOCAL_CTX)}
            except server.Refused as e:
                return {'error': str(e)}
            except Exception as e:
                return _err(e)
        return server.describe(url or f'http://localhost:{self.rest_port}/mcp')

    def _mcp_server(self):
        """Load ../mcp.py, wired to *this* Mod instance.

        By path, not by name: `import mcp` would find whatever else answers to
        that name on sys.path. Handing it this object keeps one Mod per
        process, so the tools and the functions share the stats cache instead
        of each holding their own.
        """
        if getattr(Mod, '_mcp_module', None) is None:
            import importlib.util
            path = self._dir / 'mcp.py'
            spec = importlib.util.spec_from_file_location('zcash_mcp', str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            Mod._mcp_module = module
        Mod._mcp_module._MOD = self
        return Mod._mcp_module

    def token(self) -> dict:
        """The bearer token the web app needs for spending functions."""
        state = Path(os.environ.get('ZCASH_STATE_DIR') or Path.home() / '.mod' / 'zcash')
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
               'rest': {'port': self.rest_port, 'running': self.rest_up(),
                        'serves': 'the web app'}}
        for svc, port in (('api', self.api_port), ('app', self.app_port)):
            try:
                r = requests.get(f'http://localhost:{port}', timeout=3)
                out[svc].update(running=True, code=r.status_code)
            except requests.RequestException:
                out[svc].update(running=False)
        try:
            out['chain'] = {'tip': self.chain.tip_height(),
                            'consensus_branch_id': f"{self.chain.consensus_branch_id():08x}"}
        except _chain.ChainError as e:
            out['chain'] = _err(e)
        out['wallets'] = len(_wallet.list_wallets())
        return out

    def test(self) -> dict:
        """Self-test: chain reachability, signer correctness, bridge quoting."""
        results, failures = {}, []

        try:
            tip = self.chain.tip_height()
            branch = self.chain.consensus_branch_id()
            results['chain'] = {'ok': True, 'tip': tip, 'branch_id': f"{branch:08x}"}
        except _chain.ChainError as e:
            results['chain'] = {'ok': False, 'error': str(e)}
            failures.append('chain')
            branch = _chain.FALLBACK_BRANCH_ID

        # Signer: build, sign and verify a transaction against a synthetic utxo.
        try:
            priv = _keys.HDKey.from_seed(b'zcash-selftest-seed').priv
            addr = _keys.pubkey_to_address(_keys.privkey_to_pubkey(priv))
            utxo = [{'txid': 'ab' * 32, 'vout': 0, 'value': 10 * ZAT,
                     'script_pubkey': _keys.address_to_script(addr).hex()}]
            transaction, meta = _tx.build_transaction(
                utxo, [(addr, ZAT)], addr, branch, 1_000_000)
            transaction.sign_input(0, priv)
            verified = transaction.verify_input(0)
            reparsed = _tx.parse_v5(bytes.fromhex(transaction.hex()))
            results['signer'] = {
                'ok': verified and reparsed.txid() == transaction.txid(),
                'txid': transaction.txid(), 'fee_zatoshi': meta['fee_zatoshi'],
                'signature_verified': verified,
                'round_trip': reparsed.serialize() == transaction.serialize(),
            }
            if not results['signer']['ok']:
                failures.append('signer')
        except Exception as e:
            results['signer'] = {'ok': False, 'error': str(e)}
            failures.append('signer')

        # Shielded: derive a Sapling account, pay a note to it, read it back.
        # This is the whole receive path end to end -- if it passes, an address
        # this module hands out is one whose payments it can actually find.
        try:
            xsk = _sapling.ExtendedSpendingKey.from_seed(b'zcash-selftest-seed-32bytes-long')
            fvk = xsk.fvk()
            address = fvk.address(0)
            note = _sapling.encrypt_note(address, 12_345_678, b'self test', ovk=fvk.ovk)
            opened = _sapling.decrypt_output_with_ivk(
                fvk.ivk, note['epk'], note['enc_ciphertext'], note['cmu'])
            stranger = _sapling.ExtendedSpendingKey.from_seed(b'a different seed, 32 bytes ok!!!').fvk()
            leaked = _sapling.decrypt_output_with_ivk(
                stranger.ivk, note['epk'], note['enc_ciphertext'], note['cmu'])
            results['shielded'] = {
                'ok': (opened is not None and opened.value == 12_345_678
                       and opened.memo_text() == 'self test' and leaked is None),
                'address': address.encode(),
                'unified': address.unified(),
                'note_read_back': opened.value if opened else None,
                'opaque_to_other_keys': leaked is None,
                'can_send': self.chain.has_node,
            }
            if not results['shielded']['ok']:
                failures.append('shielded')
        except Exception as e:
            results['shielded'] = {'ok': False, 'error': str(e)}
            failures.append('shielded')

        # Orchard: the same end-to-end receive path on the other pool, plus
        # the nullifier -- which is the part Orchard can do and Sapling cannot,
        # since it needs no note position and so no node.
        try:
            oxsk = _orchard.ExtendedSpendingKey.from_seed(
                b'zcash-selftest-seed-32bytes-long')
            ofvk = oxsk.fvk()
            oaddress = ofvk.address(0)
            rho = (0xC0FFEE).to_bytes(32, 'little')
            action = _orchard.encrypt_note(oaddress, 87_654_321, rho,
                                           memo=b'self test', ovk=ofvk.ovk)
            got = _orchard.decrypt_action_with_ivk(
                ofvk.ivk, rho, action['cmx'], action['epk'],
                action['enc_ciphertext'])
            other = _orchard.ExtendedSpendingKey.from_seed(
                b'a different seed, 32 bytes ok!!!').fvk()
            spilled = _orchard.decrypt_action_with_ivk(
                other.ivk, rho, action['cmx'], action['epk'],
                action['enc_ciphertext'])
            results['orchard'] = {
                'ok': (got is not None and got.value == 87_654_321
                       and got.memo_text() == 'self test' and spilled is None),
                'unified': oaddress.encode(),
                'note_read_back': got.value if got else None,
                'nullifier': got.nullifier(ofvk.nk).hex() if got else None,
                'opaque_to_other_keys': spilled is None,
                'spend_detection': 'nullifiers, no node needed',
                'can_send': self.chain.has_node,
            }
            if not results['orchard']['ok']:
                failures.append('orchard')
        except Exception as e:
            results['orchard'] = {'ok': False, 'error': str(e)}
            failures.append('orchard')

        try:
            chains = _bridge.chains()
            results['bridge'] = {'ok': True, 'chains': len(chains)}
        except _bridge.BridgeError as e:
            results['bridge'] = {'ok': False, 'error': str(e)}
            failures.append('bridge')

        results['explorer'] = {'ok': 'error' not in self.info()}
        if not results['explorer']['ok']:
            failures.append('explorer')

        results['status'] = 'ok' if not failures else 'degraded'
        results['failed'] = failures
        return results

    # ── Serve ──────────────────────────────────────────────────────────────

    def rest_up(self, port=None) -> bool:
        """Is the app's REST backend answering on its port?"""
        port = int(port or self.rest_port)
        try:
            return requests.get(f'http://127.0.0.1:{port}/health', timeout=2).ok
        except requests.RequestException:
            return False

    # pm2 keeps the REST backend and the app alive across restarts and reboots
    # (only the mod-protocol server on :50148 used to be supervised, which is
    # why the page reliably came back as "API offline" after any bounce). When
    # an entry exists, drive it through pm2 -- a bare Popen here would race the
    # supervisor for the port, and a bare SIGTERM would just be restarted.
    PM2 = {'rest': 'zcash-api', 'app': 'zcash-app'}

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

        The app is useless without this process, so starting it is not
        fire-and-forget: we report whether it actually came up rather than
        claiming success and leaving the page to 500.
        """
        port = int(port)
        if self.rest_up(port):
            return {'running': True, 'started': False, 'port': port}

        if self.PM2['rest'] in self._pm2_names():
            self._pm2_do(self.PM2['rest'], 'restart')
        else:
            env = os.environ.copy()
            env['PYTHONPATH'] = str(self._dir)
            env['PORT'] = env['ZCASH_REST_PORT'] = str(port)
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
        """Start the local REST API and the web app.

        The mod-protocol server on port 50148 is managed by the fleet; this
        starts the app's own backend (api.py) and the Next front end.
        """
        rest_port = int(rest_port or self.rest_port)
        app_port = int(app_port or self.app_port)
        self.kill()
        rest = self._start_rest(rest_port)

        self.app(port=app_port, dev=dev, rest_port=rest_port)
        return {'rest_api': f'http://127.0.0.1:{rest_port}',
                'rest': rest,
                'app': f'http://localhost:{app_port}/zcash',
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
                    'url': f'http://localhost:{port}/zcash'}

        app_log = open(self._log_dir / 'app.log', 'w')
        env = os.environ.copy()
        env['ZCASH_API_ORIGIN'] = f'http://127.0.0.1:{rest_port}'
        # so the app's /api route can restart api.py if it ever dies
        env['ZCASH_MODULE_DIR'] = str(self._dir)
        env.setdefault('NEXT_PUBLIC_BASE_PATH', '/zcash')
        cmd = ['npx', 'next', 'dev', '-p', str(port)] if dev else \
              ['npx', 'next', 'start', '-p', str(port)]
        subprocess.Popen(cmd, cwd=str(app_dir), env=env,
                         stdout=app_log, stderr=subprocess.STDOUT)
        return {'status': 'running', 'port': port, 'rest': rest,
                'url': f"http://localhost:{port}{env['NEXT_PUBLIC_BASE_PATH']}",
                'logs': str(self._log_dir / 'app.log')}

    def kill(self, service=None):
        """Stop this module's services. Only touches its own ports."""
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
            try:
                found = subprocess.run(['lsof', '-ti', f':{port}'],
                                       capture_output=True, text=True, timeout=5)
                for pid in found.stdout.split():
                    os.kill(int(pid), signal.SIGTERM)
                    killed.append(f'{svc}:{pid}')
            except (subprocess.SubprocessError, OSError, ValueError):
                continue
        return {'killed': killed}
