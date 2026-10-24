"""
What this box remembers: deployments, ABIs, and every transaction it sent.

A chain is the source of truth about state and a hopeless source of truth about
*intent*. It knows a contract exists at an address; it does not know you called
it Vault, which source produced it, which compiler settings, or that the same
caller deployed the staging copy an hour earlier. That is what this sqlite file
is for — one table per thing worth remembering, keyed by the address that did
it:

    deployments   address, chain, ABI, source, compiler settings, ctor args
    contracts     an ABI attached to an address somebody else deployed
    txs           a local receipt book: what was sent, by whom, and how it ended

Scoped by owner throughout. `owner` here is the signing address from
identity.py, not an on-chain owner — the person who asked, not the contract's
admin.

One deliberate asymmetry: looking up an ABI *by address* is not scoped. A
contract's interface is public the moment it is deployed, and pretending
otherwise would just mean two callers on one box cannot use the same token.
Who deployed what stays private; what a contract looks like does not.
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE = Path(os.path.expanduser(os.environ.get('ETH_DIR', '~/.mod/eth')))
DB_PATH = Path(os.environ.get('ETH_DB', str(STATE / 'eth.db')))

SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  name TEXT NOT NULL,
  network TEXT NOT NULL,
  chain_id INTEGER,
  address TEXT NOT NULL,
  deployer TEXT,
  tx_hash TEXT,
  block INTEGER,
  abi TEXT NOT NULL,
  bytecode TEXT,
  source TEXT,
  source_name TEXT,
  compiler TEXT,
  constructor_args TEXT,
  value TEXT,
  gas_used INTEGER,
  created INTEGER NOT NULL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS deployments_owner ON deployments(owner, created DESC);
CREATE INDEX IF NOT EXISTS deployments_address ON deployments(address, chain_id);

CREATE TABLE IF NOT EXISTS contracts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  name TEXT,
  network TEXT NOT NULL,
  chain_id INTEGER,
  address TEXT NOT NULL,
  abi TEXT NOT NULL,
  created INTEGER NOT NULL,
  UNIQUE(owner, chain_id, address)
);
CREATE INDEX IF NOT EXISTS contracts_address ON contracts(address, chain_id);

CREATE TABLE IF NOT EXISTS txs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  kind TEXT NOT NULL,
  network TEXT NOT NULL,
  chain_id INTEGER,
  hash TEXT NOT NULL,
  sender TEXT,
  recipient TEXT,
  value TEXT,
  data_size INTEGER,
  fn TEXT,
  status TEXT,
  gas_used INTEGER,
  block INTEGER,
  created INTEGER NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS txs_owner ON txs(owner, created DESC);
CREATE UNIQUE INDEX IF NOT EXISTS txs_hash ON txs(hash, chain_id);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    for field in ('abi', 'compiler', 'constructor_args'):
        if out.get(field):
            try:
                out[field] = json.loads(out[field])
            except Exception:
                pass
    return out


# ── deployments ──────────────────────────────────────────────────────

def record_deployment(owner: str, **fields) -> Dict[str, Any]:
    columns = ('owner', 'name', 'network', 'chain_id', 'address', 'deployer',
               'tx_hash', 'block', 'abi', 'bytecode', 'source', 'source_name',
               'compiler', 'constructor_args', 'value', 'gas_used', 'created', 'note')
    values = {'owner': (owner or '').lower(), 'created': int(time.time())}
    for column in columns:
        if column in fields:
            value = fields[column]
            values[column] = (json.dumps(value)
                              if column in ('abi', 'compiler', 'constructor_args')
                              and not isinstance(value, str) else value)
    values.setdefault('abi', '[]')
    with connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO deployments ({','.join(values)}) "
            f"VALUES ({','.join('?' * len(values))})", list(values.values()))
        row = conn.execute('SELECT * FROM deployments WHERE id = ?',
                           (cursor.lastrowid,)).fetchone()
    return _row(row)


def deployments(owner: str, network: Optional[str] = None,
                limit: int = 100) -> List[Dict[str, Any]]:
    query = 'SELECT * FROM deployments WHERE owner = ?'
    args: List[Any] = [(owner or '').lower()]
    if network:
        query += ' AND network = ?'
        args.append(network)
    query += ' ORDER BY created DESC LIMIT ?'
    args.append(int(limit))
    with connect() as conn:
        return [_row(r) for r in conn.execute(query, args).fetchall()]


def forget_deployment(owner: str, ident: str) -> bool:
    """Drop a row by id or address. The contract stays deployed, obviously."""
    with connect() as conn:
        cursor = conn.execute(
            'DELETE FROM deployments WHERE owner = ? AND (CAST(id AS TEXT) = ? '
            'OR LOWER(address) = LOWER(?))',
            ((owner or '').lower(), str(ident), str(ident)))
        return cursor.rowcount > 0


# ── attached ABIs ────────────────────────────────────────────────────

def attach(owner: str, address: str, abi: Any, network: str,
           chain_id: Optional[int] = None, name: Optional[str] = None) -> Dict[str, Any]:
    blob = abi if isinstance(abi, str) else json.dumps(abi)
    with connect() as conn:
        conn.execute(
            'INSERT INTO contracts (owner, name, network, chain_id, address, abi, created) '
            'VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(owner, chain_id, address) DO UPDATE SET '
            'abi = excluded.abi, name = COALESCE(excluded.name, contracts.name), '
            'network = excluded.network',
            ((owner or '').lower(), name, network, chain_id,
             address, blob, int(time.time())))
        row = conn.execute(
            'SELECT * FROM contracts WHERE owner = ? AND address = ? AND '
            'IFNULL(chain_id, -1) = IFNULL(?, -1)',
            ((owner or '').lower(), address, chain_id)).fetchone()
    return _row(row)


def attached(owner: str, network: Optional[str] = None) -> List[Dict[str, Any]]:
    query = 'SELECT * FROM contracts WHERE owner = ?'
    args: List[Any] = [(owner or '').lower()]
    if network:
        query += ' AND network = ?'
        args.append(network)
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            query + ' ORDER BY created DESC', args).fetchall()]


def detach(owner: str, address: str, chain_id: Optional[int] = None) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            'DELETE FROM contracts WHERE owner = ? AND LOWER(address) = LOWER(?)'
            + (' AND chain_id = ?' if chain_id is not None else ''),
            ([(owner or '').lower(), address] +
             ([chain_id] if chain_id is not None else [])))
        return cursor.rowcount > 0


def abi_for(address: str, chain_id: Optional[int] = None,
            owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The best ABI this box knows for an address.

    Not scoped to the caller: an interface is public. `owner` only decides
    which of several equally-good matches is preferred — yours first.
    """
    address = (address or '').lower()
    with connect() as conn:
        rows = conn.execute(
            'SELECT owner, name, abi, chain_id, network, "deployment" AS src, created '
            'FROM deployments WHERE LOWER(address) = ? '
            'UNION ALL '
            'SELECT owner, name, abi, chain_id, network, "attached" AS src, created '
            'FROM contracts WHERE LOWER(address) = ? '
            'ORDER BY created DESC', (address, address)).fetchall()
    candidates = [_row(r) for r in rows]
    if chain_id is not None:
        matching = [c for c in candidates if c.get('chain_id') in (chain_id, None)]
        candidates = matching or candidates
    if owner:
        mine = [c for c in candidates if c.get('owner') == owner.lower()]
        candidates = mine + [c for c in candidates if c not in mine]
    return candidates[0] if candidates else None


# ── the receipt book ─────────────────────────────────────────────────

def record_tx(owner: str, **fields) -> Dict[str, Any]:
    values = {'owner': (owner or '').lower(), 'created': int(time.time())}
    for column in ('kind', 'network', 'chain_id', 'hash', 'sender', 'recipient',
                   'value', 'data_size', 'fn', 'status', 'gas_used', 'block',
                   'detail'):
        if column in fields and fields[column] is not None:
            value = fields[column]
            values[column] = (json.dumps(value) if column == 'detail'
                              and not isinstance(value, str) else value)
    with connect() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO txs ({','.join(values)}) "
            f"VALUES ({','.join('?' * len(values))})", list(values.values()))
        row = conn.execute('SELECT * FROM txs WHERE hash = ? AND IFNULL(chain_id,-1) '
                           '= IFNULL(?,-1)',
                           (values.get('hash'), values.get('chain_id'))).fetchone()
    return dict(row) if row else values


def update_tx(hash_: str, chain_id: Optional[int] = None, **fields) -> None:
    if not fields:
        return
    sets = ', '.join(f'{k} = ?' for k in fields)
    args = list(fields.values()) + [hash_]
    query = f'UPDATE txs SET {sets} WHERE hash = ?'
    if chain_id is not None:
        query += ' AND chain_id = ?'
        args.append(chain_id)
    with connect() as conn:
        conn.execute(query, args)


def txs(owner: str, network: Optional[str] = None,
        limit: int = 100) -> List[Dict[str, Any]]:
    query = 'SELECT * FROM txs WHERE owner = ?'
    args: List[Any] = [(owner or '').lower()]
    if network:
        query += ' AND network = ?'
        args.append(network)
    query += ' ORDER BY created DESC LIMIT ?'
    args.append(int(limit))
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def counts(owner: str) -> Dict[str, int]:
    with connect() as conn:
        one = lambda q: conn.execute(q, ((owner or '').lower(),)).fetchone()[0]
        return {
            'deployments': one('SELECT COUNT(*) FROM deployments WHERE owner = ?'),
            'contracts': one('SELECT COUNT(*) FROM contracts WHERE owner = ?'),
            'txs': one('SELECT COUNT(*) FROM txs WHERE owner = ?'),
        }
