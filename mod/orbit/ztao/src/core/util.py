"""Felts, randomness and JSON state — shared by every network adapter."""
import hashlib
import json
import os
import re
import secrets as _secrets

from .. import DATA

PRIME = 2 ** 251 + 17 * 2 ** 192 + 1
ORDER = 0x800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f
ROLES = {'miner': 1, 'validator': 2}
BIN = os.path.expanduser('~/.local/bin')

KEYS = os.path.join(DATA, 'keys.json')            # 0600, member secrets
DEPLOYS = os.path.join(DATA, 'deployments.json')  # {network: record}
TESTS = os.path.join(DATA, 'tests.json')          # last native run per chain


def felt(v):
    """ints, decimal, 0x-hex, or any text (sha256 % PRIME) -> felt int."""
    if isinstance(v, bool):
        raise ValueError('not a felt')
    if isinstance(v, int):
        return v % PRIME
    s = str(v).strip()
    if re.fullmatch(r'\d+', s):
        return int(s) % PRIME
    if re.fullmatch(r'0x[0-9a-fA-F]+', s):
        return int(s, 16) % PRIME
    return int.from_bytes(hashlib.sha256(s.encode()).digest(), 'big') % PRIME


def rand():
    return _secrets.randbelow(ORDER - 1) + 1


def hexs(v):
    return hex(int(v))


def role_id(role):
    return ROLES[str(role)]


def env():
    e = dict(os.environ)
    e['PATH'] = BIN + os.pathsep + e.get('PATH', '')
    return e


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:  # noqa: BLE001
        return default


def save(path, obj, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 0o600 if private else 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)
