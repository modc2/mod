"""
Bitstore — Anchor CIDs from IPFS/Filecoin/Hippius/LocalFS onto Bitcoin, Kaspa, and Bittensor.

Takes content hashes from CID-based storage systems and writes them
as immutable proof-of-existence records on proof-of-work / proof-of-stake chains.

Usage (Python):
    import mod as m
    b = m.mod('bitstore')()
    b.anchor('QmXyz...', source='ipfs')
    b.from_ipfs('QmXyz...')
    b.from_localfs('/path/to/file')
    b.verify('QmXyz...')
    b.history()

Usage (CLI):
    m bitstore/anchor QmXyz... source=ipfs
    m bitstore/anchor QmXyz... source=ipfs chains=bitcoin,kaspa
    m bitstore/from_ipfs QmXyz...
    m bitstore/from_localfs /path/to/file
    m bitstore/verify QmXyz...
    m bitstore/lookup QmXyz...
    m bitstore/history
    m bitstore/history chain=bitcoin
    m bitstore/status
"""

import json
import os
import time
import hashlib
import sqlite3
import requests
from pathlib import Path
from typing import Any  # noqa: F401

DIR = Path(__file__).resolve().parent.parent  # orbit/bitstore/
STORE_DIR = Path(os.path.expanduser('~/.bitstore'))

# OP_RETURN prefix — identifies bitstore anchors on chain
TAG = 'BS'


class Mod:
    description = "Anchor CIDs from IPFS/Filecoin/Hippius/LocalFS onto Bitcoin, Kaspa, and Bittensor."

    fns = [
        'forward', 'anchor', 'verify', 'lookup', 'history', 'status',
        'sources', 'chains', 'batch_anchor',
        'bitcoin_anchor', 'kaspa_anchor', 'bittensor_anchor',
        'from_ipfs', 'from_filecoin', 'from_hippius', 'from_localfs',
    ]

    SOURCES = ['ipfs', 'filecoin', 'hippius', 'localfs']
    CHAINS = ['bitcoin', 'kaspa', 'bittensor']

    def __init__(self,
                 bitcoin_rpc=None,
                 kaspa_rpc=None,
                 kaspa_api='https://api.kaspa.org',
                 bittensor_network='finney',
                 bittensor_wallet=None,
                 netuid=None,
                 store_path=None,
                 **kw):
        self.module_dir = DIR
        self.store_dir = Path(store_path) if store_path else STORE_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store_dir / 'anchors.db'

        # Chain configs
        self.bitcoin_rpc = bitcoin_rpc or os.environ.get('BITCOIN_RPC')
        self.kaspa_rpc = kaspa_rpc or os.environ.get('KASPA_RPC')
        self.kaspa_api = kaspa_api
        self.bittensor_network = bittensor_network
        self.bittensor_wallet = bittensor_wallet
        self.netuid = int(netuid) if netuid else None

        self._init_db()
        self.config = self._load_config()

    def _load_config(self):
        cfg = self.module_dir / 'config.json'
        if cfg.exists():
            with open(cfg) as f:
                return json.load(f)
        return {}

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('''CREATE TABLE IF NOT EXISTS anchors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cid TEXT NOT NULL,
            source TEXT NOT NULL,
            chain TEXT NOT NULL,
            txid TEXT,
            block_height INTEGER,
            timestamp INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            payload TEXT,
            meta TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_anchors_cid ON anchors(cid)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_anchors_chain ON anchors(chain)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_anchors_source ON anchors(source)')
        conn.commit()
        conn.close()

    def _db(self):
        return sqlite3.connect(str(self.db_path))

    # ── Core ──────────────────────────────────────────────────────

    def forward(self, cid: str = None, source: str = 'ipfs', chains: str = None, **kw):
        """Default entry point. Anchor a CID or show status."""
        if not cid:
            return self.status()
        chain_list = [c.strip() for c in chains.split(',')] if chains else self.CHAINS
        return self.anchor(cid, source=source, chains=chain_list)

    def anchor(self, cid: str, source: str = 'ipfs', chains: list = None, meta: dict = None) -> dict:
        """Anchor a CID on one or more chains.

        Args:
            cid: Content identifier (IPFS Qm..., bafy..., Filecoin deal CID, etc.)
            source: Origin — ipfs, filecoin, hippius, localfs
            chains: Target chains (default: all three)
            meta: Optional metadata
        """
        if chains is None:
            chains = list(self.CHAINS)
        if isinstance(chains, str):
            chains = [c.strip() for c in chains.split(',')]

        results = {}
        for chain in chains:
            chain = chain.strip().lower()
            fn = {
                'bitcoin': self._anchor_bitcoin,
                'kaspa': self._anchor_kaspa,
                'bittensor': self._anchor_bittensor,
            }.get(chain)
            if not fn:
                results[chain] = {'error': f'unsupported chain: {chain}'}
                continue
            try:
                results[chain] = fn(cid, source, meta)
            except Exception as e:
                results[chain] = self._record(cid, source, chain, None, 'failed', meta, error=str(e))

        return {'cid': cid, 'source': source, 'anchors': results}

    def verify(self, cid: str, chain: str = None) -> dict:
        """Check if a CID is anchored. Optionally filter by chain."""
        conn = self._db()
        if chain:
            rows = conn.execute(
                'SELECT * FROM anchors WHERE cid=? AND chain=? ORDER BY timestamp DESC',
                (cid, chain.lower())
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM anchors WHERE cid=? ORDER BY timestamp DESC', (cid,)
            ).fetchall()
        conn.close()
        anchors = [self._row_to_dict(r) for r in rows]
        return {'cid': cid, 'anchored': len(anchors) > 0, 'count': len(anchors), 'anchors': anchors}

    def lookup(self, cid: str) -> dict:
        """Alias for verify."""
        return self.verify(cid)

    def history(self, limit: int = 50, chain: str = None, source: str = None) -> list:
        """Show recent anchoring history."""
        conn = self._db()
        q = 'SELECT * FROM anchors WHERE 1=1'
        params = []
        if chain:
            q += ' AND chain=?'; params.append(chain.lower())
        if source:
            q += ' AND source=?'; params.append(source.lower())
        q += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(int(limit))
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [self._row_to_dict(r) for r in rows]

    def batch_anchor(self, cids: str = '[]', source: str = 'ipfs', chains: str = None) -> list:
        """Anchor multiple CIDs."""
        if isinstance(cids, str):
            cids = json.loads(cids)
        chain_list = [c.strip() for c in chains.split(',')] if chains else None
        return [self.anchor(cid, source=source, chains=chain_list) for cid in cids]

    def status(self) -> dict:
        """Bitstore status — chain connectivity and anchor counts."""
        conn = self._db()
        total = conn.execute('SELECT COUNT(*) FROM anchors').fetchone()[0]
        by_chain = {c: conn.execute('SELECT COUNT(*) FROM anchors WHERE chain=?', (c,)).fetchone()[0] for c in self.CHAINS}
        by_source = {s: conn.execute('SELECT COUNT(*) FROM anchors WHERE source=?', (s,)).fetchone()[0] for s in self.SOURCES}
        conn.close()
        return {
            'name': 'bitstore',
            'store': str(self.store_dir),
            'total_anchors': total,
            'by_chain': by_chain,
            'by_source': by_source,
            'chains': {
                'bitcoin': self._bitcoin_status(),
                'kaspa': self._kaspa_status(),
                'bittensor': self._bittensor_status(),
            },
        }

    def sources(self) -> list:
        return list(self.SOURCES)

    def chains(self) -> list:
        return list(self.CHAINS)

    # ── Source helpers ─────────────────────────────────────────────

    def from_ipfs(self, cid: str, chains: str = None) -> dict:
        """Anchor an IPFS CID."""
        c = [x.strip() for x in chains.split(',')] if chains else None
        return self.anchor(cid, source='ipfs', chains=c)

    def from_filecoin(self, cid: str, chains: str = None) -> dict:
        """Anchor a Filecoin CID."""
        c = [x.strip() for x in chains.split(',')] if chains else None
        return self.anchor(cid, source='filecoin', chains=c)

    def from_hippius(self, cid: str, chains: str = None) -> dict:
        """Anchor a Hippius CID."""
        c = [x.strip() for x in chains.split(',')] if chains else None
        return self.anchor(cid, source='hippius', chains=c)

    def from_localfs(self, path: str, chains: str = None) -> dict:
        """Hash a local file to CID and anchor it."""
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Not found: {path}")
        with open(path, 'rb') as f:
            data = f.read()
        # CIDv1-style hash (bafk prefix + sha256)
        cid = 'bafk' + hashlib.sha256(data).hexdigest()
        c = [x.strip() for x in chains.split(',')] if chains else None
        return self.anchor(cid, source='localfs', chains=c, meta={'path': path, 'size': len(data)})

    # ── Bitcoin ───────────────────────────────────────────────────

    def _build_payload(self, cid: str, source: str) -> str:
        """Build the on-chain payload. Fits in 80 bytes (OP_RETURN limit)."""
        # Format: BS:<src4>:<cid_or_hash>
        # If the full CID fits in 80 bytes, use it directly.
        # Otherwise truncate to sha256 hex prefix (40 chars).
        payload = f"{TAG}:{source[:4]}:{cid}"
        if len(payload.encode()) <= 80:
            return payload
        cid_hash = hashlib.sha256(cid.encode()).hexdigest()[:40]
        return f"{TAG}:{source[:4]}:{cid_hash}"

    def _anchor_bitcoin(self, cid: str, source: str, meta: dict = None) -> dict:
        payload = self._build_payload(cid, source)
        if self.bitcoin_rpc:
            try:
                txid = self._bitcoin_op_return(payload)
                return self._record(cid, source, 'bitcoin', txid, 'confirmed', meta, payload=payload)
            except Exception as e:
                return self._record(cid, source, 'bitcoin', None, 'failed', meta, payload=payload, error=str(e))
        # Offline mode — record locally, broadcast later
        return self._record(cid, source, 'bitcoin', None, 'pending', meta, payload=payload)

    def _bitcoin_op_return(self, payload: str) -> str:
        from bitcoinrpc.authproxy import AuthServiceProxy
        rpc = AuthServiceProxy(self.bitcoin_rpc)
        payload_hex = payload.encode('utf-8').hex()
        if len(payload_hex) > 160:
            raise ValueError(f"Payload too large for OP_RETURN: {len(payload_hex)//2} bytes")
        change_addr = rpc.getnewaddress()
        unspent = rpc.listunspent(1, 9999999)
        if not unspent:
            raise Exception("No UTXOs available")
        utxo = max(unspent, key=lambda u: float(u['amount']))
        inputs = [{'txid': utxo['txid'], 'vout': utxo['vout']}]
        fee = 0.0001
        outputs = {'data': payload_hex, change_addr: round(float(utxo['amount']) - fee, 8)}
        raw_tx = rpc.createrawtransaction(inputs, outputs)
        signed = rpc.signrawtransactionwithwallet(raw_tx)
        if not signed['complete']:
            raise Exception("Signing failed")
        return rpc.sendrawtransaction(signed['hex'])

    def bitcoin_anchor(self, cid: str, source: str = 'ipfs') -> dict:
        return self.anchor(cid, source=source, chains=['bitcoin'])

    def _bitcoin_status(self) -> dict:
        if not self.bitcoin_rpc:
            return {'connected': False, 'mode': 'offline'}
        try:
            from bitcoinrpc.authproxy import AuthServiceProxy
            rpc = AuthServiceProxy(self.bitcoin_rpc)
            info = rpc.getblockchaininfo()
            return {'connected': True, 'chain': info.get('chain'), 'blocks': info.get('blocks')}
        except Exception as e:
            return {'connected': False, 'error': str(e)}

    # ── Kaspa ─────────────────────────────────────────────────────

    def _anchor_kaspa(self, cid: str, source: str, meta: dict = None) -> dict:
        payload = self._build_payload(cid, source)
        if self.kaspa_rpc:
            try:
                txid = self._kaspa_submit(payload)
                return self._record(cid, source, 'kaspa', txid, 'confirmed', meta, payload=payload)
            except Exception as e:
                return self._record(cid, source, 'kaspa', None, 'failed', meta, payload=payload, error=str(e))
        # Offline mode
        return self._record(cid, source, 'kaspa', None, 'pending', meta, payload=payload)

    def _kaspa_submit(self, payload: str) -> str:
        """Submit transaction with script data to Kaspa via RPC."""
        payload_hex = payload.encode('utf-8').hex()
        # Kaspa wRPC / gRPC submit-transaction
        resp = requests.post(f"{self.kaspa_rpc}/api/v1/transactions", json={
            'script_public_key': {'script': payload_hex},
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('transactionId') or data.get('txid') or data.get('id')

    def kaspa_anchor(self, cid: str, source: str = 'ipfs') -> dict:
        return self.anchor(cid, source=source, chains=['kaspa'])

    def _kaspa_status(self) -> dict:
        try:
            resp = requests.get(f"{self.kaspa_api}/info/virtual-chain-blue-score", timeout=5)
            if resp.ok:
                data = resp.json()
                return {'connected': True, 'blue_score': data.get('blueScore'), 'api': self.kaspa_api}
        except Exception:
            pass
        return {'connected': False, 'api': self.kaspa_api}

    # ── Bittensor ─────────────────────────────────────────────────

    def _anchor_bittensor(self, cid: str, source: str, meta: dict = None) -> dict:
        payload = self._build_payload(cid, source)
        try:
            import bittensor as bt
            txid = self._bittensor_commit(payload, bt)
            return self._record(cid, source, 'bittensor', txid, 'confirmed', meta, payload=payload)
        except ImportError:
            return self._record(cid, source, 'bittensor', None, 'pending', meta,
                                payload=payload, error='pip install bittensor')
        except Exception as e:
            return self._record(cid, source, 'bittensor', None, 'failed', meta,
                                payload=payload, error=str(e))

    def _bittensor_commit(self, payload: str, bt) -> str:
        """Write commitment to Bittensor via set_commitment (bt v10+)."""
        wallet_name = self.bittensor_wallet or 'default'
        wallet = bt.Wallet(name=wallet_name)
        sub = bt.Subtensor(network=self.bittensor_network)
        netuid = self.netuid or 1

        result = sub.set_commitment(
            wallet, netuid, payload,
            wait_for_inclusion=True,
            wait_for_finalization=False,
        )

        # Check ExtrinsicResponse success
        if hasattr(result, 'success') and not result.success:
            msg = getattr(result, 'message', str(result))
            raise Exception(f"Bittensor commit failed: {msg}")

        if hasattr(result, 'extrinsic_hash') and result.extrinsic_hash:
            return result.extrinsic_hash
        return str(result) if result else None

    def bittensor_anchor(self, cid: str, source: str = 'ipfs') -> dict:
        return self.anchor(cid, source=source, chains=['bittensor'])

    def _bittensor_status(self) -> dict:
        try:
            import bittensor as bt
            sub = bt.Subtensor(network=self.bittensor_network)
            block = sub.get_current_block()
            return {'connected': True, 'block': block, 'network': self.bittensor_network}
        except ImportError:
            return {'connected': False, 'note': 'pip install bittensor'}
        except Exception as e:
            return {'connected': False, 'error': str(e)}

    # ── Internal ──────────────────────────────────────────────────

    def _record(self, cid, source, chain, txid, status, meta=None, payload=None, error=None) -> dict:
        ts = int(time.time())
        meta_dict = dict(meta) if meta else {}
        if error:
            meta_dict['error'] = error
        meta_json = json.dumps(meta_dict) if meta_dict else None

        conn = self._db()
        conn.execute(
            'INSERT INTO anchors (cid,source,chain,txid,timestamp,status,payload,meta) VALUES (?,?,?,?,?,?,?,?)',
            (cid, source, chain, txid, ts, status, payload, meta_json)
        )
        conn.commit()
        conn.close()

        out = {'chain': chain, 'txid': txid, 'status': status, 'timestamp': ts}
        if payload:
            out['payload'] = payload
        if error:
            out['error'] = error
        return out

    def _row_to_dict(self, r) -> dict:
        return {
            'id': r[0], 'cid': r[1], 'source': r[2], 'chain': r[3],
            'txid': r[4], 'block_height': r[5], 'timestamp': r[6],
            'status': r[7], 'payload': r[8],
            'meta': json.loads(r[9]) if r[9] else None,
        }
