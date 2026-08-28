"""
vault — the metadata index for circuits and messages.

What lives here (~/.mod/encrypt, off-tree, never committed):

    circuits/<id>.py    the circuit source the user brought
    circuits.json       {id: {name, owner, sha256, public, cid, ...}}
    messages.json       {id: {owner, circuit, cid, bytes, label, burn, ...}}

What deliberately does NOT live here: keys, passphrases, plaintext. A message
row points at a store CID and names the circuit that produced it — nothing in
this file can decrypt anything.
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

DEFAULT_DIR = '~/.mod/encrypt'
_LOCK = threading.Lock()


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or '{}')
    except json.JSONDecodeError:
        return {}


def _write(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Vault:

    def __init__(self, path: str = DEFAULT_DIR):
        self.dir = Path(os.path.expanduser(os.environ.get('ENCRYPT_DIR', path)))
        self.circuit_dir = self.dir / 'circuits'
        self.circuit_dir.mkdir(parents=True, exist_ok=True)
        self.circuits_index = self.dir / 'circuits.json'
        self.messages_index = self.dir / 'messages.json'

    # ── circuits ─────────────────────────────────────────────────────

    def circuit_id(self, source: bytes) -> str:
        return 'c' + digest(source)[:16]

    def source_path(self, cid_or_id: str) -> Path:
        return self.circuit_dir / f'{cid_or_id}.py'

    def circuits(self) -> dict:
        return _read(self.circuits_index)

    def circuit(self, cid: str) -> Optional[dict]:
        return self.circuits().get(cid)

    def source(self, cid: str) -> Optional[str]:
        p = self.source_path(cid)
        return p.read_text() if p.exists() else None

    def add_circuit(self, source: bytes, name: str, owner: str, public: bool = False,
                    cid: Optional[str] = None, selftest: Optional[dict] = None) -> dict:
        cid_local = self.circuit_id(source)
        with _LOCK:
            index = self.circuits()
            existing = index.get(cid_local)
            self.source_path(cid_local).write_text(source.decode('utf-8', 'replace'))
            row = {
                'id': cid_local,
                'name': name,
                'owner': owner.lower(),
                'sha256': digest(source),
                'bytes': len(source),
                'public': bool(public),
                'cid': cid or (existing or {}).get('cid'),
                'selftest': selftest or (existing or {}).get('selftest'),
                'created_at': (existing or {}).get('created_at') or int(time.time()),
                'updated_at': int(time.time()),
            }
            index[cid_local] = row
            _write(self.circuits_index, index)
        return row

    def rm_circuit(self, cid: str) -> bool:
        with _LOCK:
            index = self.circuits()
            row = index.pop(cid, None)
            _write(self.circuits_index, index)
        self.source_path(cid).unlink(missing_ok=True)
        return row is not None

    def circuit_in_use(self, cid: str) -> int:
        return sum(1 for msg in self.messages().values() if msg.get('circuit') == cid)

    # ── messages ─────────────────────────────────────────────────────

    def messages(self) -> dict:
        return _read(self.messages_index)

    def message(self, mid: str) -> Optional[dict]:
        return self.messages().get(mid)

    def add_message(self, row: dict) -> dict:
        with _LOCK:
            index = self.messages()
            index[row['id']] = row
            _write(self.messages_index, index)
        return row

    def update_message(self, mid: str, **fields) -> Optional[dict]:
        with _LOCK:
            index = self.messages()
            row = index.get(mid)
            if row is None:
                return None
            row.update(fields)
            _write(self.messages_index, index)
        return row

    def rm_message(self, mid: str) -> bool:
        with _LOCK:
            index = self.messages()
            row = index.pop(mid, None)
            _write(self.messages_index, index)
        return row is not None

    def owned(self, index: dict, owner: str) -> list:
        owner = (owner or '').lower()
        return sorted((r for r in index.values() if r.get('owner') == owner),
                      key=lambda r: r.get('created_at', 0), reverse=True)

    def stats(self) -> dict:
        msgs = self.messages()
        return {
            'circuits': len(self.circuits()),
            'messages': len(msgs),
            'ciphertext_bytes': sum(int(m.get('bytes') or 0) for m in msgs.values()),
            'dir': str(self.dir),
        }
