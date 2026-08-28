#!/usr/bin/env python3
"""solana programs — deploy one, load one, and call it.

A Solana program is an ELF of sBPF bytecode living in an account, and almost
everything people find hard about it is that the chain will not tell you what
is in there. This module answers the three questions in order:

  load     what IS this program — which loader owns it, who can still upgrade
           it, how big is the code, which syscalls does it use, and does it
           publish an IDL that names its instructions?
  deploy   put an ELF on chain: buffer account, hundreds of Write transactions,
           then DeployWithMaxDataLen — the same dance `solana program deploy`
           does, here as a background job you can watch.
  invoke   build one instruction against it — by name and arguments when there
           is an IDL, by raw bytes when there is not — and simulate it before
           (or instead of) signing it.

The ELF can come from a file on this box, from base64 in the request, or from
another cluster: `clone` reads the deployed bytes of a live mainnet program and
redeploys them to devnet, which is the fastest way to get something real to
play with when there is no Rust toolchain in reach.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import string
import threading
import time
import zlib

import keys as K
from chain import (ATA_PROGRAM, KNOWN, MEMO, SYSTEM, TOKEN, TOKEN_2022,
                   Client, sol)
from keys import (SolError, b58decode, b58encode, find_program_address,
                  is_address, need_address, signer)

# ── the loaders ──────────────────────────────────────────────────

UPGRADEABLE = 'BPFLoaderUpgradeab1e11111111111111111111111'
LOADER2 = 'BPFLoader2111111111111111111111111111111111'
LOADER1 = 'BPFLoader1111111111111111111111111111111111'
LOADER4 = 'LoaderV411111111111111111111111111111111111'
NATIVE = 'NativeLoader1111111111111111111111111111111'
RENT = 'SysvarRent111111111111111111111111111111111'
CLOCK = 'SysvarC1ock11111111111111111111111111111111'

LOADERS = {UPGRADEABLE: 'BPF Upgradeable Loader', LOADER2: 'BPF Loader 2',
           LOADER1: 'BPF Loader (deprecated)', LOADER4: 'Loader v4',
           NATIVE: 'native — built into the validator, not an ELF'}

# UpgradeableLoaderState, borsh-serialised, is a 4-byte enum tag then the body.
STATE_BUFFER, STATE_PROGRAM, STATE_PROGRAMDATA = 1, 2, 3
BUFFER_HEADER = 37          # tag(4) + Option<Pubkey> authority(1+32)
PROGRAM_SIZE = 36           # tag(4) + Pubkey programdata(32)
PROGRAMDATA_HEADER = 45     # tag(4) + slot(8) + Option<Pubkey> authority(1+32)

# A packet is 1232 bytes and a Write carries three account keys, a blockhash and
# one signature; 900 leaves room for all of it without arithmetic per program.
CHUNK = 900
# Reads go through `confirmed`, not the RPC default of `finalized`: finalized
# lags by half a minute, long enough that a program you just deployed reads
# back as missing and the deploy looks like it failed.
LATEST = {'encoding': 'base64', 'commitment': 'confirmed'}
IDL_DIR = os.path.join(K.KEY_DIR, 'idl')
IDL_SEED = 'anchor:idl'

# Programs worth cloning to devnet when you just want something real to call.
SAMPLES = {
    'memo': 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr',
    'spl_token': TOKEN,
    'ata': 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL',
    'name_service': 'namesLPneVptA9Z5rqUDD9tMTWEJwofgaYwp8cawRkX',
    'lighthouse': 'L2TExMFKdjpN9kozasaurPirfHy9P8sbXoAN1qA3S95',
}


# ── ELF ──────────────────────────────────────────────────────────

# 247 is EM_BPF, what the old toolchain emitted; 263 is EM_SBPF, what it emits
# now. A program built this year and one built in 2021 differ right here.
MACHINES = {247: 'BPF', 263: 'SBPF'}


def elf_length(raw):
    """Where the ELF actually ends.

    A deployed program is padded with zeros out to whatever headroom the deploy
    reserved, and you cannot just strip them: the section header table ends in
    legitimate zero bytes, so rstrip eats it and the file stops parsing. Ask
    the headers instead.
    """
    if len(raw) < 64 or raw[:4] != b'\x7fELF':
        return len(raw.rstrip(b'\x00'))

    def u16(off):
        return int.from_bytes(raw[off:off + 2], 'little')

    def u64(off):
        return int.from_bytes(raw[off:off + 8], 'little')

    end = 64
    phoff, phentsize, phnum = u64(32), u16(54), u16(56)
    shoff, shentsize, shnum = u64(40), u16(58), u16(60)
    end = max(end, phoff + phentsize * phnum, shoff + shentsize * shnum)
    if shoff and shoff + shnum * shentsize <= len(raw):
        for i in range(shnum):
            base = shoff + i * shentsize
            if int.from_bytes(raw[base + 4:base + 8], 'little') != 8:   # not NOBITS
                end = max(end, u64(base + 24) + u64(base + 32))
    return min(end, len(raw))


def elf_info(raw, strings=True):
    """Read the header and section table of an sBPF ELF.

    Enough to answer 'is this actually a Solana program, and what does it do' —
    the syscalls a program imports say more about its powers than its size
    does: sol_invoke_signed_ means it signs for PDAs, and its absence means it
    cannot move anyone else's tokens.
    """
    out = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if len(raw) < 64 or raw[:4] != b'\x7fELF':
        out['valid'] = False
        out['problem'] = ('not an ELF — a Solana program is a 64-bit little-endian '
                          'ELF for machine 247 (BPF), the .so that '
                          '`cargo build-sbf` writes into target/deploy')
        return out

    def u16(off):
        return int.from_bytes(raw[off:off + 2], 'little')

    def u32(off):
        return int.from_bytes(raw[off:off + 4], 'little')

    def u64(off):
        return int.from_bytes(raw[off:off + 8], 'little')

    machine = u16(18)
    out.update({'valid': True, 'class': '64-bit' if raw[4] == 2 else '32-bit',
                'endian': 'little' if raw[5] == 1 else 'big',
                'type': {1: 'REL', 2: 'EXEC', 3: 'DYN (shared object)'}.get(u16(16)),
                'machine': machine, 'machine_name': MACHINES.get(machine),
                'entry': u64(24)})
    if machine not in MACHINES:
        out['valid'] = False
        out['problem'] = (f'machine is {machine}, not 247 (BPF) or 263 (SBPF) — '
                          'this ELF was not built for Solana and the loader '
                          'will refuse it')

    shoff, shentsize, shnum, shstrndx = u64(40), u16(58), u16(60), u16(62)
    sections, by_name = [], {}
    if shoff and shnum and shoff + shnum * shentsize <= len(raw):
        head = shoff + shstrndx * shentsize
        strtab_off, strtab_size = u64(head + 24), u64(head + 32)
        names = raw[strtab_off:strtab_off + strtab_size]

        def name_at(idx):
            end = names.find(b'\x00', idx)
            return names[idx:end if end >= 0 else None].decode('utf-8', 'replace')

        for i in range(shnum):
            base = shoff + i * shentsize
            sec = {'name': name_at(u32(base)), 'type': u32(base + 4),
                   'addr': u64(base + 16), 'offset': u64(base + 24),
                   'size': u64(base + 32)}
            sections.append(sec)
            by_name[sec['name']] = sec
    out['sections'] = [{'name': s['name'], 'bytes': s['size']}
                       for s in sections if s['name']]
    text = by_name.get('.text')
    out['code_bytes'] = text['size'] if text else None
    out['instructions'] = int(text['size'] // 8) if text else None   # sBPF slots

    def section_bytes(sec):
        if not sec or sec['type'] == 8:                              # SHT_NOBITS
            return b''
        return raw[sec['offset']:sec['offset'] + sec['size']]

    dynstr = section_bytes(by_name.get('.dynstr'))
    symbols = [s.decode('utf-8', 'replace') for s in dynstr.split(b'\x00') if s]
    out['syscalls'] = sorted({s for s in symbols
                              if s.startswith('sol_') or s in ('abort', 'memcpy',
                                                               'memset', 'memcmp')})
    out['exports'] = [s for s in symbols if s in ('entrypoint',)] or None
    if strings:
        out['strings'] = _strings(section_bytes(by_name.get('.rodata')))
    return out


def _strings(blob, minimum=8, cap=60):
    """Printable runs out of .rodata — a program's log lines and error messages,
    which is the closest thing to documentation an ELF carries."""
    ok = (string.ascii_letters + string.digits + string.punctuation + ' ').encode()
    found, run = [], bytearray()
    for byte in blob:
        if byte in ok:
            run.append(byte)
            continue
        if len(run) >= minimum:
            found.append(run.decode('utf-8', 'replace'))
        run = bytearray()
    if len(run) >= minimum:
        found.append(run.decode('utf-8', 'replace'))
    seen, out = set(), []
    for s in found:
        s = s.strip()
        if s and s not in seen and not re.fullmatch(r'[\W_]+', s):
            seen.add(s)
            out.append(s)
    return out[:cap]


# ── borsh ────────────────────────────────────────────────────────

_INTS = {'u8': (1, False), 'i8': (1, True), 'u16': (2, False), 'i16': (2, True),
         'u32': (4, False), 'i32': (4, True), 'u64': (8, False), 'i64': (8, True),
         'u128': (16, False), 'i128': (16, True), 'u256': (32, False),
         'i256': (32, True)}


def _type_name(t):
    """Both IDL dialects in one shape: 'u64', 'publicKey' (anchor <=0.29) and
    'pubkey' (0.30+), {'defined': 'Foo'} and {'defined': {'name': 'Foo'}}."""
    if not isinstance(t, str):
        return t
    low = t.lower()
    if low in _INTS or low in ('bool', 'string', 'bytes', 'f32', 'f64'):
        return low
    if low in ('pubkey', 'publickey'):
        return 'pubkey'
    return t


def _defined(t):
    if isinstance(t, dict) and 'defined' in t:
        d = t['defined']
        return d if isinstance(d, str) else (d or {}).get('name')
    return None


def encode(t, value, types=None):
    """Borsh-encode one value against an IDL type."""
    types = types or {}
    t = _type_name(t)
    if isinstance(t, str):
        if t in _INTS:
            size, signed = _INTS[t]
            if isinstance(value, str):
                value = int(value, 0)
            return int(value).to_bytes(size, 'little', signed=signed)
        if t == 'bool':
            return bytes([1 if value else 0])
        if t in ('f32', 'f64'):
            import struct
            return struct.pack('<f' if t == 'f32' else '<d', float(value))
        if t == 'string':
            blob = str(value).encode()
            return len(blob).to_bytes(4, 'little') + blob
        if t == 'bytes':
            blob = _bytes_of(value)
            return len(blob).to_bytes(4, 'little') + blob
        if t in ('pubkey', 'publicKey'):
            return b58decode(need_address(value, 'pubkey'))
        if t in types:
            return _encode_defined(types[t], value, types)
        raise SolError(f'no encoder for type {t!r}')
    name = _defined(t)
    if name:
        if name not in types:
            raise SolError(f'the IDL references type {name!r} but does not define it')
        return _encode_defined(types[name], value, types)
    if 'option' in t or 'coption' in t:
        inner = t.get('option') or t.get('coption')
        if value is None:
            return b'\x00' * (4 if 'coption' in t else 1)
        prefix = (1).to_bytes(4, 'little') if 'coption' in t else b'\x01'
        return prefix + encode(inner, value, types)
    if 'vec' in t:
        items = list(value or [])
        return len(items).to_bytes(4, 'little') + b''.join(
            encode(t['vec'], v, types) for v in items)
    if 'array' in t:
        inner, count = t['array']
        items = list(value or [])
        if isinstance(count, dict):                     # generic length: rare
            count = len(items)
        if len(items) != count:
            raise SolError(f'this field is a fixed array of {count}, '
                           f'got {len(items)} values')
        return b''.join(encode(inner, v, types) for v in items)
    raise SolError(f'no encoder for type {json.dumps(t)}')


def _encode_defined(typedef, value, types):
    body = typedef.get('type') if isinstance(typedef, dict) and 'type' in typedef \
        else typedef
    kind = (body or {}).get('kind')
    if kind == 'struct':
        if not isinstance(value, dict):
            raise SolError(f'{typedef.get("name")} is a struct — pass an object with '
                           + ', '.join(f['name'] for f in body.get('fields') or []))
        return b''.join(encode(f['type'], value.get(f['name']), types)
                        for f in body.get('fields') or [])
    if kind == 'enum':
        variants = body.get('variants') or []
        if isinstance(value, str):
            name, fields = value, None
        elif isinstance(value, dict) and len(value) == 1:
            name, fields = next(iter(value.items()))
        else:
            raise SolError('an enum is either its variant name, or '
                           '{"VariantName": {...fields}}')
        for i, v in enumerate(variants):
            if v['name'].lower() == str(name).lower():
                out = bytes([i])
                for f in v.get('fields') or []:
                    if isinstance(f, dict):
                        out += encode(f['type'], (fields or {}).get(f['name']), types)
                    else:
                        out += encode(f, fields, types)
                return out
        raise SolError(f'{name!r} is not one of ' +
                       ', '.join(v['name'] for v in variants))
    if kind == 'type':                                   # a plain alias
        return encode(body.get('alias') or body.get('type'), value, types)
    raise SolError(f'cannot encode {json.dumps(typedef)[:120]}')


class _Reader:
    def __init__(self, raw):
        self.raw, self.at = raw, 0

    def take(self, n):
        if self.at + n > len(self.raw):
            raise SolError('the account data ran out mid-field — either this is '
                           'not that account type, or the IDL is stale')
        out = self.raw[self.at:self.at + n]
        self.at += n
        return out


def decode(t, reader, types=None):
    """Borsh-decode one value. Big integers come back as strings, because JSON
    numbers stop being exact well before u64 does."""
    types = types or {}
    t = _type_name(t)
    if isinstance(t, str):
        if t in _INTS:
            size, signed = _INTS[t]
            n = int.from_bytes(reader.take(size), 'little', signed=signed)
            return str(n) if size > 6 and abs(n) > 2 ** 53 else n
        if t == 'bool':
            return reader.take(1)[0] == 1
        if t in ('f32', 'f64'):
            import struct
            return struct.unpack('<f' if t == 'f32' else '<d',
                                 reader.take(4 if t == 'f32' else 8))[0]
        if t == 'string':
            return reader.take(int.from_bytes(reader.take(4), 'little')).decode(
                'utf-8', 'replace')
        if t == 'bytes':
            return b58encode(reader.take(int.from_bytes(reader.take(4), 'little')))
        if t in ('pubkey', 'publicKey'):
            return b58encode(reader.take(32))
        if t in types:
            return _decode_defined(types[t], reader, types)
        raise SolError(f'no decoder for type {t!r}')
    name = _defined(t)
    if name:
        if name not in types:
            raise SolError(f'the IDL references type {name!r} but does not define it')
        return _decode_defined(types[name], reader, types)
    if 'option' in t:
        return decode(t['option'], reader, types) if reader.take(1)[0] else None
    if 'coption' in t:
        return decode(t['coption'], reader, types) \
            if int.from_bytes(reader.take(4), 'little') else None
    if 'vec' in t:
        n = int.from_bytes(reader.take(4), 'little')
        return [decode(t['vec'], reader, types) for _ in range(n)]
    if 'array' in t:
        inner, count = t['array']
        if inner in ('u8', 'i8') and isinstance(count, int):
            return binascii.hexlify(reader.take(count)).decode()
        return [decode(inner, reader, types) for _ in range(count)]
    raise SolError(f'no decoder for type {json.dumps(t)}')


def _decode_defined(typedef, reader, types):
    body = typedef.get('type') if isinstance(typedef, dict) and 'type' in typedef \
        else typedef
    kind = (body or {}).get('kind')
    if kind == 'struct':
        return {f['name']: decode(f['type'], reader, types)
                for f in body.get('fields') or []}
    if kind == 'enum':
        variants = body.get('variants') or []
        i = reader.take(1)[0]
        if i >= len(variants):
            raise SolError(f'enum tag {i} is past the last variant')
        v = variants[i]
        fields = v.get('fields') or []
        if not fields:
            return v['name']
        return {v['name']: {f['name']: decode(f['type'], reader, types)
                            for f in fields if isinstance(f, dict)}}
    raise SolError(f'cannot decode {json.dumps(typedef)[:120]}')


def _bytes_of(value):
    """Bytes from whatever a JSON caller had at hand: hex, base64, base58, or a
    list of ints. Guessing is fine here because the three encodings of real
    instruction data almost never collide."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, list):
        return bytes(int(b) & 0xFF for b in value)
    if isinstance(value, dict):
        for key in ('text', 'utf8', 'string'):
            if key in value:
                return str(value[key]).encode()
        for key in ('hex', 'base64', 'b64', 'base58', 'b58', 'bytes'):
            if key in value:
                return _bytes_of(f'{key}:{value[key]}')
    text = str(value or '').strip()
    if not text:
        return b''
    # An explicit prefix beats the guess below, and text: is the only way to
    # say "these characters, literally" — a memo is not base58 by accident.
    tag, _, rest = text.partition(':')
    if rest and tag.lower() in ('text', 'utf8', 'utf-8', 'string'):
        return rest.encode()
    if rest and tag.lower() in ('hex', '0x'):
        return binascii.unhexlify(rest.strip())
    if rest and tag.lower() in ('b64', 'base64'):
        return base64.b64decode(rest.strip())
    if rest and tag.lower() in ('b58', 'base58'):
        return b58decode(rest.strip())
    if text.startswith('0x'):
        return binascii.unhexlify(text[2:])
    if re.fullmatch(r'[0-9a-fA-F]+', text) and len(text) % 2 == 0:
        return binascii.unhexlify(text)
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        pass
    try:
        return b58decode(text)
    except Exception:
        raise SolError(f'cannot read {text[:24]!r} as bytes — pass hex, base64, '
                       'base58, a list of byte values, or text:<the literal '
                       'characters>')


def discriminator(kind, name):
    """Anchor's 8-byte tag: sha256("global:the_instruction")[:8] for calls,
    sha256("account:TheAccount")[:8] for state."""
    return hashlib.sha256(f'{kind}:{name}'.encode()).digest()[:8]


def _snake(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


# ── IDL ──────────────────────────────────────────────────────────

def create_with_seed(base, seed, owner):
    return b58encode(hashlib.sha256(
        b58decode(base) + seed.encode() + b58decode(owner)).digest())


def idl_address(program_id):
    """Where anchor parks a program's IDL: a seed-derived account owned by the
    program itself, so the interface travels with the code."""
    base = find_program_address([], program_id)[0]
    return create_with_seed(base, IDL_SEED, program_id)


def local_idl_path(program_id):
    return os.path.join(IDL_DIR, f'{program_id}.json')


def load_idl(client, program_id, on_chain=True):
    """The IDL for a program: the copy saved on this box first (it may be newer,
    or may be the only one), then the account anchor writes on chain."""
    program_id = need_address(program_id, 'program')
    path = local_idl_path(program_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f), 'local'
        except Exception:
            pass
    if not on_chain:
        return None, None
    address = idl_address(program_id)
    value = (client.call('getAccountInfo', [address, LATEST]) or {}).get('value')
    if not value:
        return None, None
    raw = base64.b64decode((value.get('data') or [''])[0])
    # 8-byte discriminator, 32-byte authority, u32 length, then zlib.
    body = raw[44:]
    length = int.from_bytes(raw[40:44], 'little') if len(raw) >= 44 else 0
    for candidate in (body[:length] if length else b'', body,
                      raw[raw.find(b'\x78'):] if b'\x78' in raw[:64] else b''):
        if not candidate:
            continue
        try:
            return json.loads(zlib.decompress(candidate)), 'chain'
        except Exception:
            continue
    raise SolError(f'an account exists at {address}, the address anchor would '
                   'use for this program\'s IDL, but it does not hold '
                   'zlib-compressed JSON — treat this program as having no IDL',
                   status=502)


def save_idl(program_id, idl):
    program_id = need_address(program_id, 'program')
    if isinstance(idl, str):
        idl = json.loads(idl)
    if not isinstance(idl, dict) or 'instructions' not in idl:
        raise SolError('that does not look like an anchor IDL — it needs an '
                       '"instructions" array')
    os.makedirs(IDL_DIR, exist_ok=True)
    with open(local_idl_path(program_id), 'w') as f:
        json.dump(idl, f, indent=1)
    return {'program': program_id, 'saved': local_idl_path(program_id),
            'instructions': len(idl.get('instructions') or [])}


def idl_types(idl):
    return {t['name']: t for t in (idl.get('types') or []) if t.get('name')}


def idl_summary(idl):
    """The IDL as a menu: what you can call, and what each call wants."""
    if not idl:
        return None
    meta = idl.get('metadata') or {}
    return {
        'name': idl.get('name') or meta.get('name'),
        'version': idl.get('version') or meta.get('version'),
        'spec': meta.get('spec') or 'legacy',
        'address': idl.get('address'),
        'instructions': [{
            'name': i['name'],
            'args': [{'name': a['name'], 'type': _readable(a['type'])}
                     for a in i.get('args') or []],
            'accounts': [{'name': a.get('name'),
                          'signer': bool(a.get('signer') or a.get('isSigner')),
                          'writable': bool(a.get('writable') or a.get('isMut')),
                          'optional': bool(a.get('optional') or a.get('isOptional')),
                          'derived': bool(a.get('pda') or a.get('address'))}
                         for a in _flat_accounts(i.get('accounts') or [])],
        } for i in idl.get('instructions') or []],
        'accounts': [a.get('name') for a in idl.get('accounts') or []],
        'errors': [{'code': e.get('code'), 'name': e.get('name'),
                    'msg': e.get('msg')} for e in (idl.get('errors') or [])[:60]],
        'types': [t.get('name') for t in idl.get('types') or []],
    }


def _flat_accounts(accounts):
    """Anchor nests composite account groups; the wire format does not."""
    out = []
    for a in accounts:
        if isinstance(a, dict) and a.get('accounts'):
            out.extend(_flat_accounts(a['accounts']))
        else:
            out.append(a)
    return out


def _readable(t):
    t = _type_name(t)
    if isinstance(t, str):
        return t
    name = _defined(t)
    if name:
        return name
    for k in ('option', 'coption', 'vec'):
        if k in t:
            return f'{k}<{_readable(t[k])}>'
    if 'array' in t:
        return f'[{_readable(t["array"][0])}; {t["array"][1]}]'
    return json.dumps(t)


def find_instruction(idl, name):
    for i in idl.get('instructions') or []:
        if i['name'] == name or _snake(i['name']) == _snake(str(name)):
            return i
    raise SolError(f'{name!r} is not an instruction of this program — it has ' +
                   ', '.join(i['name'] for i in idl.get('instructions') or []))


def instruction_data(idl, name, args):
    """Discriminator plus borsh-encoded arguments, in IDL order."""
    ix = find_instruction(idl, name)
    disc = bytes(ix['discriminator']) if ix.get('discriminator') else \
        discriminator('global', _snake(ix['name']))
    args = args or {}
    if isinstance(args, list):
        args = {a['name']: v for a, v in zip(ix.get('args') or [], args)}
    body = b''
    for a in ix.get('args') or []:
        if a['name'] not in args and not isinstance(a.get('type'), dict):
            raise SolError(f'{ix["name"]} needs the argument {a["name"]} '
                           f'({_readable(a["type"])})')
        body += encode(a['type'], args.get(a['name']), idl_types(idl))
    return disc + body, ix


def decode_account(idl, raw, hint=None):
    """Match an account's first 8 bytes against the IDL and read the struct."""
    types = idl_types(idl)
    for acc in idl.get('accounts') or []:
        disc = bytes(acc['discriminator']) if acc.get('discriminator') else \
            discriminator('account', acc['name'])
        if hint and acc['name'] != hint:
            continue
        if raw[:8] != disc:
            continue
        body = acc.get('type') or types.get(acc['name'])
        if not body:
            raise SolError(f'the IDL names an account {acc["name"]} but does not '
                           'define its fields')
        return {'type': acc['name'],
                'data': _decode_defined(body, _Reader(raw[8:]), types)}
    return None


# ── reading a deployed program ───────────────────────────────────

def program_info(client, address, code=False, strings=True, idl=True):
    """Everything the chain knows about a deployed program."""
    address = need_address(SAMPLES.get(str(address).lower(), address), 'program')
    value = (client.call('getAccountInfo', [address, LATEST]) or {}).get('value')
    if value is None:
        return {'network': client.network, 'program': address, 'exists': False,
                'note': 'nothing is deployed at this address on '
                        f'{client.network} — programs are per-cluster, so check '
                        'the network before you conclude it is gone'}
    owner = value.get('owner')
    raw = base64.b64decode((value.get('data') or [''])[0])
    out = {'network': client.network, 'program': address, 'exists': True,
           'executable': bool(value.get('executable')), 'loader': owner,
           'loader_name': LOADERS.get(owner) or KNOWN.get(owner),
           'lamports': value.get('lamports'), 'sol': sol(value.get('lamports')),
           'known_as': KNOWN.get(address)}
    if not value.get('executable') and owner in LOADERS and \
            len(raw) >= 4 and int.from_bytes(raw[:4], 'little') == STATE_BUFFER:
        out.update(_buffer_state(raw, address))
        return out
    if not value.get('executable'):
        out['note'] = ('this account is not executable — it is data owned by '
                       f'{KNOWN.get(owner) or owner}, not a program. sol_account '
                       'says what it is.')
        return out

    elf = b''
    if owner == UPGRADEABLE:
        pd = b58encode(raw[4:36]) if len(raw) >= 36 else None
        out['programdata'] = pd
        info = (client.call('getAccountInfo', [pd, LATEST]) or {}).get('value') \
            if pd else None
        if not info:
            out['problem'] = ('the program points at a programdata account that '
                              'does not exist — it has been closed, and the '
                              'address can never be redeployed')
            return out
        blob = base64.b64decode((info.get('data') or [''])[0])
        slot = int.from_bytes(blob[4:12], 'little')
        has_auth = blob[12] == 1
        authority = b58encode(blob[13:45]) if has_auth else None
        elf = blob[PROGRAMDATA_HEADER:]
        out.update({
            'upgradeable': has_auth, 'upgrade_authority': authority,
            'immutable': not has_auth,
            'last_deployed_slot': slot,
            'programdata_bytes': len(blob),
            'max_data_bytes': len(blob) - PROGRAMDATA_HEADER,
            'rent_sol': sol((info.get('lamports') or 0) + (value.get('lamports') or 0)),
            'authority_note': ('anyone holding that key can replace this code '
                               'without notice' if has_auth else
                               'the upgrade authority is revoked — this code is '
                               'frozen and cannot be changed'),
        })
    elif owner in (LOADER1, LOADER2):
        elf = raw
        out.update({'upgradeable': False, 'immutable': True,
                    'authority_note': 'deployed with the old loader, so it was '
                                      'immutable from the moment it landed'})
    elif owner == NATIVE:
        out['note'] = 'a native program — it lives in the validator, not in an ELF'
        return out

    if elf:
        # Everything past elf_length is the headroom a later upgrade grows into.
        size = elf_length(elf)
        out['headroom_bytes'] = len(elf) - size
        elf = elf[:size]
        out['elf'] = elf_info(elf, strings=strings)
        if code:
            out['elf_base64'] = base64.b64encode(elf).decode()
    if idl:
        try:
            doc, source = load_idl(client, address)
        except SolError as e:
            doc, source, out['idl_error'] = None, None, str(e)
        out['idl_source'] = source
        out['idl'] = idl_summary(doc)
        if not doc:
            out['idl_note'] = ('no IDL published — call it with raw instruction '
                               'data, or POST one to /idl and this module will '
                               'use it from then on')
    return out


def _buffer_state(raw, address):
    has_auth = len(raw) > 4 and raw[4] == 1
    return {'kind': 'buffer', 'executable': False,
            'buffer_authority': b58encode(raw[5:37]) if has_auth else None,
            'staged_bytes': len(raw) - BUFFER_HEADER,
            'note': 'a write buffer from an unfinished deploy — it holds an ELF '
                    'and rent. Deploy from it, or close it to get the rent back.'}


def program_elf(client, address):
    """The deployed bytes themselves, ready to hash, save, or redeploy."""
    info = program_info(client, address, code=True, strings=False, idl=False)
    if not info.get('exists'):
        raise SolError(f'no program at {address} on {client.network}', status=404)
    if not info.get('elf_base64'):
        raise SolError(f'{address} is {info.get("loader_name") or "not a BPF program"}'
                       ' — there are no ELF bytes to read', status=400)
    return base64.b64decode(info['elf_base64']), info


def program_accounts(client, program, idl=None, limit=25, account_type=None,
                     decode_data=True):
    """Every account the program owns, decoded through its IDL where possible.

    This is the state a program has actually accumulated — the thing that makes
    a deployed program more than a blob of code.
    """
    program = need_address(program, 'program')
    filters = []
    if account_type and idl:
        for acc in idl.get('accounts') or []:
            if acc['name'].lower() == str(account_type).lower():
                disc = bytes(acc['discriminator']) if acc.get('discriminator') else \
                    discriminator('account', acc['name'])
                filters.append({'memcmp': {'offset': 0,
                                           'bytes': b58encode(disc)}})
    cfg = {'encoding': 'base64', 'withContext': False, 'commitment': 'confirmed'}
    if filters:
        cfg['filters'] = filters
    try:
        rows = client.call('getProgramAccounts', [program, cfg]) or []
    except SolError as e:
        raise SolError(f'this RPC will not list accounts for {program}: {e} — '
                       'public nodes cap getProgramAccounts on large programs. '
                       'Pass rpc= with your own endpoint.', status=e.status or 400)
    out = []
    for row in rows[:int(limit)]:
        acct = row.get('account') or {}
        raw = base64.b64decode((acct.get('data') or [''])[0])
        item = {'address': row.get('pubkey'), 'bytes': len(raw),
                'sol': sol(acct.get('lamports')),
                'discriminator': binascii.hexlify(raw[:8]).decode()}
        if decode_data and idl:
            try:
                hit = decode_account(idl, raw)
                if hit:
                    item.update(hit)
            except SolError as e:
                item['decode_error'] = str(e)
        out.append(item)
    return {'network': client.network, 'program': program, 'count': len(rows),
            'shown': len(out), 'accounts': out}


# ── instructions the loader understands ──────────────────────────

def _u32(n):
    return int(n).to_bytes(4, 'little')


def _u64(n):
    return int(n).to_bytes(8, 'little')


def programdata_of(program):
    return find_program_address([b58decode(program)], UPGRADEABLE)[0]


def create_account_ix(payer, new, lamports, space, owner):
    return (SYSTEM, [(payer, True, True), (new, True, True)],
            _u32(0) + _u64(lamports) + _u64(space) + b58decode(owner))


def init_buffer_ix(buffer, authority):
    return (UPGRADEABLE, [(buffer, False, True), (authority, False, False)], _u32(0))


def write_ix(buffer, authority, offset, chunk):
    # bincode, not borsh: the loader's own instructions are the one place on
    # Solana where a Vec length is eight bytes rather than four.
    return (UPGRADEABLE, [(buffer, False, True), (authority, True, False)],
            _u32(1) + _u32(offset) + _u64(len(chunk)) + bytes(chunk))


def deploy_ix(payer, program, buffer, authority, max_data_len):
    return (UPGRADEABLE,
            [(payer, True, True), (programdata_of(program), False, True),
             (program, False, True), (buffer, False, True), (RENT, False, False),
             (CLOCK, False, False), (SYSTEM, False, False), (authority, True, False)],
            _u32(2) + _u64(max_data_len))


def upgrade_ix(program, buffer, authority, spill):
    return (UPGRADEABLE,
            [(programdata_of(program), False, True), (program, False, True),
             (buffer, False, True), (spill, False, True), (RENT, False, False),
             (CLOCK, False, False), (authority, True, False)], _u32(3))


def set_authority_ix(account, current, new=None):
    metas = [(account, False, True), (current, True, False)]
    if new:
        metas.append((new, False, False))
    return (UPGRADEABLE, metas, _u32(4))


def close_ix(account, recipient, authority, program=None):
    metas = [(account, False, True), (recipient, False, True), (authority, True, False)]
    if program:
        metas.append((program, False, True))
    return (UPGRADEABLE, metas, _u32(5))


# ── deploy jobs ──────────────────────────────────────────────────
# A real deploy is one buffer, a few hundred Write transactions and a final
# Deploy — minutes of work over a public RPC. Doing that inside a request would
# time out somewhere in the middle and leave nobody holding the buffer key, so
# it runs as a job you can watch, and every key it generates is written to the
# keystore BEFORE it is used.

JOBS = {}
_LOCK = threading.Lock()
JOB_CAP = 60


def _job_new(kind, **fields):
    jid = f'{kind}-{int(time.time())}-{os.urandom(2).hex()}'
    job = {'id': jid, 'kind': kind, 'status': 'starting', 'phase': 'preparing',
           'started': time.time(), 'updated': time.time(), 'steps': [],
           'transactions': [], **fields}
    with _LOCK:
        JOBS[jid] = job
        for old in sorted(JOBS.values(), key=lambda j: j['started'])[:-JOB_CAP]:
            JOBS.pop(old['id'], None)
    return job


def _step(job, text, **fields):
    job['steps'].append({'at': round(time.time() - job['started'], 1), 'say': text})
    job.update(fields)
    job['updated'] = time.time()


def job_view(job):
    out = {k: v for k, v in job.items() if not k.startswith('_')}
    out['elapsed_s'] = round((job.get('finished') or time.time()) - job['started'], 1)
    if job.get('chunks_total'):
        out['progress_pct'] = round(
            100 * (job.get('chunks_done') or 0) / job['chunks_total'], 1)
    return out


def job(jid):
    found = JOBS.get(jid)
    if not found:
        raise SolError(f'no job {jid} — jobs live in the server process, so a '
                       'restart forgets them. sol_deploy action=list shows what '
                       'is left.', status=404)
    return job_view(found)


def job_list(limit=20):
    rows = sorted(JOBS.values(), key=lambda j: j['started'], reverse=True)
    return {'count': len(rows),
            'jobs': [{k: v for k, v in job_view(j).items()
                      if k in ('id', 'kind', 'status', 'phase', 'network', 'program',
                               'buffer', 'progress_pct', 'error', 'elapsed_s',
                               'signature')} for j in rows[:int(limit)]]}


def elf_source(client, path=None, data=None, clone=None, clone_network='mainnet',
               rpc=None):
    """Where the bytes come from: a file on this box, base64 in the request, or
    a program already deployed on another cluster."""
    if clone:
        clone = SAMPLES.get(str(clone).lower(), clone)
        src = Client(network=clone_network, rpc=rpc)
        raw, info = program_elf(src, clone)
        return raw, {'from': 'clone', 'cloned_from': clone,
                     'cloned_network': src.network,
                     'cloned_authority': info.get('upgrade_authority')}
    if data:
        return _bytes_of(data), {'from': 'upload'}
    if path:
        path = os.path.expanduser(str(path))
        if not os.path.exists(path):
            raise SolError(f'no file at {path} — cargo build-sbf writes the .so '
                           'into target/deploy/', status=404)
        with open(path, 'rb') as f:
            return f.read(), {'from': 'file', 'path': path}
    raise SolError('nothing to deploy — give path= (a .so on this box), data= '
                   '(base64 of one), or clone= (a program address on another '
                   'cluster, or one of: ' + ', '.join(SAMPLES) + ')')


def deploy(client, path=None, data=None, clone=None, clone_network='mainnet',
           program=None, buffer=None, wallet=None, secret=None, max_data_len=None,
           confirm=False, wait=0, upgrade=None, name=None):
    """Put an ELF on chain, or replace the one that is there.

    Returns a job immediately; `wait` seconds blocks for a short deploy so a
    one-shot caller gets the answer without polling.
    """
    seed, payer = signer(wallet, secret)
    raw, origin = (b'', {}) if buffer and not (path or data or clone) else \
        elf_source(client, path, data, clone, clone_network)
    if raw:
        info = elf_info(raw, strings=False)
        if not info.get('valid'):
            raise SolError(info.get('problem') or 'that is not a Solana program ELF')
    else:
        info = {}

    upgrading = bool(upgrade or (program and not buffer)) if program else False
    if program:
        program = need_address(program, 'program')
        current = program_info(client, program, strings=False, idl=False)
        upgrading = current.get('exists') and current.get('executable')
        if upgrading:
            if current.get('loader') != UPGRADEABLE:
                raise SolError(f'{program} was deployed with '
                               f'{current.get("loader_name")} — that loader has no '
                               'upgrade path. Deploy to a new address.', status=400)
            if current.get('immutable'):
                raise SolError(f'{program} is immutable — its upgrade authority was '
                               'revoked, so nobody can replace this code, '
                               'including you.', status=400)
            if current.get('upgrade_authority') != payer:
                raise SolError(
                    f'{program} can only be upgraded by '
                    f'{current.get("upgrade_authority")}, and this transaction '
                    f'would be signed by {payer}', status=403)
            room = current.get('max_data_bytes') or 0
            if raw and len(raw) > room:
                raise SolError(f'the new ELF is {len(raw):,} bytes but this program '
                               f'was deployed with room for {room:,} — Solana '
                               'cannot grow a programdata account, so this one '
                               'has to be redeployed at a new address', status=400)
        program_seed = None

    if client.network == 'mainnet' and not confirm:
        raise SolError('a mainnet deploy spends real SOL and publishes code '
                       'permanently — call it again with confirm=true', status=400)

    size = len(raw) if raw else 0
    max_len = int(max_data_len or (size * 2 if not upgrading else size))
    have = (client.call('getBalance', [payer, {'commitment': 'confirmed'}])
            or {}).get('value') or 0
    cost = 0 if upgrading else client.rent(PROGRAMDATA_HEADER + max_len) + \
        client.rent(PROGRAM_SIZE)
    staging = client.rent(BUFFER_HEADER + size) if raw and not buffer else 0
    fees = 5000 * (size // CHUNK + 4)
    needed = cost + staging + fees
    if have < needed:
        raise SolError(
            f'{payer} holds {sol(have)} SOL; this deploy needs about '
            f'{sol(needed)} — {sol(cost)} of rent the program keeps, '
            f'{sol(staging)} staged in the buffer and returned when it lands, '
            f'and {sol(fees)} of fees. ' +
            ('Fund it from a faucet: sol_airdrop.'
             if client.network != 'mainnet' else ''), status=400)

    if not program:
        # A program keypair is worth more than the rent it carries: lose it
        # before the deploy lands and that address can never be used. So it
        # goes into the keystore first, and only then into a transaction.
        program, program_seed, entry = _program_key(client, name)
        origin['program_wallet'] = entry

    job = _job_new('upgrade' if upgrading else 'deploy',
                   network=client.network, rpc=client.rpc,
                   program=program, buffer=buffer,
                   authority=payer, payer=payer, elf_bytes=size,
                   elf_sha256=info.get('sha256'), max_data_len=max_len,
                   cost_sol=sol(needed), chunks_total=(size + CHUNK - 1) // CHUNK if raw
                   else 0, chunks_done=0, **origin)
    seeds = {payer: seed}
    if program_seed:
        seeds[program] = program_seed
    thread = threading.Thread(target=_run, daemon=True,
                              args=(job, client, raw, seeds, upgrading, max_len))
    thread.start()
    deadline = time.time() + float(wait or 0)
    while time.time() < deadline and job['status'] in ('starting', 'running'):
        time.sleep(0.4)
    return job_view(job)


def _program_key(client, name):
    """The keypair the program will live at: a named one you already made (so a
    failed deploy can be retried onto the same address), or a fresh one."""
    existing = {w['name']: w for w in (K.wallets().get('wallets') or [])}
    if name and name in existing:
        address = existing[name]['address']
        live = program_info(client, address, strings=False, idl=False)
        if live.get('exists'):
            raise SolError(f'the keystore wallet {name!r} is {address}, and '
                           'something is already deployed there — pass '
                           f'program={address} to upgrade it, or a different '
                           'name', status=400)
        return address, signer(name)[0], name

    base = name or 'program'
    label = base if base not in existing else \
        next(f'{base}-{n}' for n in range(2, 999) if f'{base}-{n}' not in existing)
    entry = K.create(label)
    return entry['address'], signer(label)[0], label


def _run(job, client, elf, seeds, upgrading, max_len):
    payer, program = job['payer'], job['program']
    try:
        job['status'] = 'running'
        buffer = job.get('buffer')
        if elf:
            if not buffer:
                buffer = _make_buffer(job, client, seeds, len(elf))
            _write_all(job, client, seeds, buffer, elf)
            _verify(job, client, seeds, buffer, elf)
        if not buffer:
            raise SolError('no buffer to deploy from')
        _step(job, 'deploying from the buffer' if not upgrading
              else 'swapping the code in', phase='deploy')
        ix = upgrade_ix(program, buffer, payer, payer) if upgrading else \
            deploy_ix(payer, program, buffer, payer, max_len)
        pre = [] if upgrading else [
            create_account_ix(payer, program, client.rent(PROGRAM_SIZE),
                              PROGRAM_SIZE, UPGRADEABLE)]
        sent = client.send_ix(payer, pre + [ix], seeds, wait=True, seconds=60)
        job['transactions'].append({'step': 'deploy', **sent})
        job['signature'] = sent['signature']
        job['explorer'] = sent['explorer']
        if not (sent.get('confirmation') or {}).get('confirmed'):
            raise SolError(f'the deploy transaction did not confirm: '
                           f'{json.dumps(sent.get("confirmation"))}')
        _step(job, 'live', phase='done', status='done', buffer=None,
              finished=time.time(), program_url=f'/solana#{program}')
        for key in (job.get('_buffer_wallet'),):
            if key:
                try:
                    K.remove(key)                     # the buffer is spent
                except Exception:
                    pass
        job['deployed'] = program_info(client, program, strings=False, idl=False)
    except Exception as e:
        job.update({'status': 'failed', 'error': str(e), 'finished': time.time(),
                    'recover': _recovery(job)})
        _step(job, f'failed: {e}', phase='failed')


def _recovery(job):
    if job.get('buffer'):
        return (f'the buffer {job["buffer"]} still holds the bytes and the rent. '
                f'Retry with buffer={job["buffer"]} to finish the deploy, or '
                f'sol_authority action=close account={job["buffer"]} to get the '
                'rent back.')
    return 'nothing was written on chain — no rent is stranded.'


def _make_buffer(job, client, seeds, size):
    entry = K.create(f'buffer-{int(time.time())}')
    buffer, buffer_seed = entry['address'], signer(entry['name'])[0]
    seeds[buffer] = buffer_seed
    job['_buffer_wallet'] = entry['name']
    job['buffer'] = buffer
    space = BUFFER_HEADER + size
    _step(job, f'opening a {space:,} byte buffer at {buffer}', phase='buffer')
    sent = client.send_ix(job['payer'], [
        create_account_ix(job['payer'], buffer, client.rent(space), space,
                          UPGRADEABLE),
        init_buffer_ix(buffer, job['payer'])], seeds, wait=True)
    job['transactions'].append({'step': 'buffer', **sent})
    if not (sent.get('confirmation') or {}).get('confirmed'):
        raise SolError('the buffer account never confirmed — nothing was written')
    return buffer


def _write_all(job, client, seeds, buffer, elf, only=None):
    """Fire the Write transactions. They are independent, so they go out without
    waiting for each other; correctness comes from the read-back afterwards."""
    offsets = only if only is not None else list(range(0, len(elf), CHUNK))
    _step(job, f'writing {len(elf):,} bytes in {len(offsets)} transactions',
          phase='write')
    blockhash, refreshed = client.blockhash(), time.time()
    done = 0
    for offset in offsets:
        if time.time() - refreshed > 25:              # a blockhash lasts ~60s
            blockhash, refreshed = client.blockhash(), time.time()
        chunk = elf[offset:offset + CHUNK]
        for attempt in range(4):
            try:
                client.send_ix(job['payer'],
                               [write_ix(buffer, job['payer'], offset, chunk)],
                               seeds, wait=False, blockhash=blockhash,
                               skip_preflight=True)
                break
            except SolError as e:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
                blockhash, refreshed = client.blockhash(), time.time()
        done += 1
        job.update({'chunks_done': (job.get('chunks_done') or 0) + 1,
                    'written_bytes': min(len(elf),
                                         (job.get('written_bytes') or 0) + len(chunk)),
                    'updated': time.time()})
        time.sleep(0.06)                              # public RPCs are rate-limited
    return done


def _verify(job, client, seeds, buffer, elf, rounds=4):
    """Read the buffer back and rewrite whatever did not land.

    Transactions get dropped, and a program with one missing chunk fails to
    deploy with an error that says nothing about which chunk. Comparing bytes
    is cheaper than guessing.
    """
    for attempt in range(rounds):
        time.sleep(6 if attempt == 0 else 3)
        _step(job, 'reading the buffer back', phase='verify')
        # Explicitly at `confirmed`: the default commitment for a read is
        # `finalized`, which lags the writes by half a minute and reports an
        # account that plainly exists as missing.
        value = (client.call('getAccountInfo', [buffer, LATEST]) or {}).get('value')
        if not value:
            if attempt == rounds - 1:
                raise SolError(f'the buffer {buffer} is not there — the account '
                               'creation never landed')
            continue
        on_chain = base64.b64decode((value.get('data') or [''])[0])[BUFFER_HEADER:]
        missing = [off for off in range(0, len(elf), CHUNK)
                   if on_chain[off:off + CHUNK] != elf[off:off + CHUNK]]
        job['missing_chunks'] = len(missing)
        if not missing:
            _step(job, 'every byte is on chain', bytes_verified=len(elf))
            return True
        if attempt == rounds - 1:
            raise SolError(f'{len(missing)} of '
                           f'{(len(elf) + CHUNK - 1) // CHUNK} chunks never landed '
                           'after four passes — the RPC is dropping transactions. '
                           'Retry with buffer=' + buffer + ' to resume.')
        _step(job, f'{len(missing)} chunks missing — rewriting them')
        job['chunks_done'] = max(0, (job.get('chunks_total') or 0) - len(missing))
        _write_all(job, client, seeds, buffer, elf, only=missing)
    return False


def authority(client, action, account=None, new_authority=None, wallet=None,
              secret=None, recipient=None, confirm=False, payer_wallet=None):
    """Change or revoke who can upgrade a program, or close a buffer/program
    and take the rent back. Every one of these is irreversible."""
    seed, me = signer(wallet, secret)
    account = need_address(account, 'account')
    seeds = {me: seed}
    # The authority key often holds nothing — it is a permission, not a wallet —
    # and a fee it cannot pay comes back from the node as 'attempt to debit an
    # account but found no record of a prior credit', which explains nothing.
    payer = me
    if payer_wallet:
        pseed, payer = signer(payer_wallet)
        seeds[payer] = pseed
    if ((client.call('getBalance', [payer, {'commitment': 'confirmed'}])
         or {}).get('value') or 0) < 10_000:
        raise SolError(f'{payer} holds no SOL and cannot pay the fee for this — '
                       'fund it, or pass payer_wallet= with a keystore wallet '
                       'that can', status=400)
    info = program_info(client, account, strings=False, idl=False)
    target = account
    if info.get('exists') and info.get('executable'):
        if info.get('loader') != UPGRADEABLE:
            raise SolError(f'{account} is under {info.get("loader_name")}, which has '
                           'no authority to change', status=400)
        target = info['programdata']
    if action in ('set', 'transfer'):
        new = need_address(new_authority, 'new_authority')
        if not confirm:
            return {'would': f'hand the upgrade authority for {account} to {new}',
                    'needs_confirm': True,
                    'warning': 'that key could then replace this code at will'}
        ix = set_authority_ix(target, me, new)
    elif action in ('revoke', 'freeze', 'final'):
        if not confirm:
            return {'would': f'revoke the upgrade authority for {account}',
                    'needs_confirm': True,
                    'warning': 'this is permanent — the code can never be changed '
                               'again, by you or anyone'}
        ix = set_authority_ix(target, me, None)
    elif action == 'close':
        if not confirm:
            return {'would': f'close {account} and send its rent to '
                             f'{recipient or me}', 'needs_confirm': True,
                    'warning': 'a closed program address can never be used again'}
        is_program = info.get('exists') and info.get('executable')
        ix = close_ix(target, need_address(recipient or me, 'recipient'), me,
                      account if is_program else None)
    else:
        raise SolError(f'action must be set, revoke or close — got {action!r}')
    sent = client.send_ix(payer, [ix], seeds, wait=True)
    return {'network': client.network, 'account': account, 'action': action,
            'authority': me, 'new_authority': new_authority if action == 'set' else
            (None if action in ('revoke', 'freeze', 'final') else None), **sent}


# ── calling a program ────────────────────────────────────────────

SYSVAR_IX = 'Sysvar1nstructions1111111111111111111111111'
WELL_KNOWN = {
    'system': SYSTEM, 'systemprogram': SYSTEM, 'system_program': SYSTEM,
    'rent': RENT, 'clock': CLOCK, 'instructions': SYSVAR_IX,
    'token': TOKEN, 'tokenprogram': TOKEN, 'token_program': TOKEN,
    'token2022': TOKEN_2022, 'token_2022': TOKEN_2022,
    'ata': ATA_PROGRAM, 'associatedtokenprogram': ATA_PROGRAM,
    'associated_token_program': ATA_PROGRAM, 'memo': MEMO,
}
SELF_NAMES = {'self', 'me', 'signer', 'payer', 'wallet', 'authority', 'user',
              'owner', 'from'}


def seed_bytes(value):
    """A PDA seed the way a program writes it: `b"text"`, a pubkey's 32 bytes,
    or an integer's little-endian bytes — never borsh's length prefix."""
    if isinstance(value, dict):
        for key in ('pubkey', 'publicKey', 'address'):
            if key in value:
                return b58decode(need_address(value[key], 'seed'))
        for key, size in (('u8', 1), ('u16', 2), ('u32', 4), ('u64', 8),
                          ('u128', 16)):
            if key in value:
                return int(value[key]).to_bytes(size, 'little')
        if 'string' in value or 'text' in value:
            return str(value.get('string') or value.get('text')).encode()
        if 'bytes' in value or 'hex' in value:
            return _bytes_of(value.get('bytes') or value.get('hex'))
        raise SolError(f'cannot read {json.dumps(value)} as a seed')
    if isinstance(value, list):
        return bytes(int(b) & 0xFF for b in value)
    if isinstance(value, bool):
        return bytes([1 if value else 0])
    if isinstance(value, int):
        return value.to_bytes(8, 'little')
    text = str(value)
    # An address-shaped string is almost always meant as its 32 bytes; text
    # seeds are short words like "vault". Say which was used, in the answer.
    if is_address(text) and len(text) >= 32:
        return b58decode(text)
    return text.encode()


def pda(seeds, program, network=None):
    """Derive a program address from seeds — the thing every anchor program
    does off-chain before it can call itself."""
    program = need_address(program, 'program')
    blobs = [seed_bytes(s) for s in (seeds if isinstance(seeds, list) else [seeds])]
    for i, blob in enumerate(blobs):
        if len(blob) > 32:
            raise SolError(f'seed {i} is {len(blob)} bytes — the runtime caps a '
                           'seed at 32')
    address, bump = find_program_address(blobs, program)
    return {'address': address, 'bump': bump, 'program': program,
            'seeds': [{'as': 'pubkey' if len(b) == 32 and not _is_text(b) else
                       ('text' if _is_text(b) else 'bytes'),
                       'value': (b.decode() if _is_text(b) else
                                 (b58encode(b) if len(b) == 32 else b.hex())),
                       'bytes': len(b)} for b in blobs],
            'note': 'off the ed25519 curve, so only this program can sign for it'}


def _is_text(blob):
    try:
        text = blob.decode('ascii')
    except Exception:
        return False
    return bool(text) and all(c in string.printable and c not in '\x0b\x0c'
                              for c in text)


def _resolve_account(item, me, program, args, idl, resolved):
    """One account meta out of whatever the caller wrote."""
    if isinstance(item, str):
        flags, _, text = item.rpartition(':')
        writable = 'w' in flags.lower()
        is_signer = 's' in flags.lower()
        return _address_of(text, me, program), is_signer, writable
    if isinstance(item, dict):
        text = item.get('pubkey') or item.get('address') or item.get('account')
        if item.get('seeds'):
            text = pda(item['seeds'], item.get('program') or program)['address']
        return (_address_of(text, me, program),
                bool(item.get('signer') or item.get('isSigner')),
                bool(item.get('writable') or item.get('isMut') or item.get('mut')))
    raise SolError(f'cannot read {json.dumps(item)} as an account')


def _address_of(text, me, program):
    text = str(text or '').strip()
    key = text.lower().replace('-', '_')
    if key in SELF_NAMES:
        if not me:
            raise SolError(f'{text!r} means "the signing wallet", but no wallet was '
                           'given — pass wallet=, or the address itself')
        return me
    if key in WELL_KNOWN:
        return WELL_KNOWN[key]
    if key in ('program', 'programid', 'program_id', 'this'):
        return program
    return need_address(text, 'account')


def _idl_accounts(ix_def, given, me, program, args, idl):
    """Order and flag the accounts from the IDL, filling in what it can derive.

    An anchor IDL names its accounts and says which are signers, writable, or
    PDAs of known seeds — so the caller only has to supply the ones that are
    genuinely their choice. Derivation runs in passes, because an IDL routinely
    lists a PDA before the account whose key seeds it.
    """
    given = {str(k).lower().replace('-', '_'): v for k, v in (given or {}).items()}
    slots, resolved = [], {}
    for acc in _flat_accounts(ix_def.get('accounts') or []):
        name = acc.get('name')
        key = _snake(str(name)).lower()
        supplied = given.get(key, given.get(str(name).lower()))
        address, how = None, 'you'
        if supplied is not None:
            address = pda(supplied['seeds'], supplied.get('program') or program
                          )['address'] if isinstance(supplied, dict) and \
                supplied.get('seeds') else _address_of(
                    supplied if isinstance(supplied, str) else
                    (supplied.get('pubkey') or supplied.get('address')),
                    me, program)
        elif acc.get('address'):
            address, how = acc['address'], 'idl'            # the IDL pins it
        elif key in WELL_KNOWN:
            address, how = WELL_KNOWN[key], 'well-known'
        elif me and (key in SELF_NAMES or _is_caller(key)):
            # Only where the name says it means the caller. Filling every empty
            # signer slot with the wallet would silently point an instruction at
            # the wrong account and blame the program for the failure.
            address, how = me, 'wallet'
        if address:
            resolved[key] = address
        slots.append({'acc': acc, 'name': name, 'key': key, 'address': address,
                      'how': how})

    for _pass in range(3):
        progress = False
        for slot in slots:
            if slot['address'] or not slot['acc'].get('pda'):
                continue
            slot['address'] = _derive_idl_pda(slot['acc']['pda'], args, resolved,
                                              program, idl)
            if slot['address']:
                slot['how'] = 'pda'
                resolved[slot['key']] = slot['address']
                progress = True
        if not progress:
            break

    metas, names, hows, needed = [], [], [], []
    for slot in slots:
        acc = slot['acc']
        if not slot['address']:
            if acc.get('optional') or acc.get('isOptional'):
                continue
            needed.append({'name': slot['name'],
                           'signer': bool(acc.get('signer') or acc.get('isSigner')),
                           'writable': bool(acc.get('writable') or acc.get('isMut'))})
            continue
        names.append(slot['name'])
        hows.append(slot['how'])
        metas.append((slot['address'], bool(acc.get('signer') or acc.get('isSigner')),
                      bool(acc.get('writable') or acc.get('isMut'))))
    if needed:
        raise SolError(
            f'{ix_def["name"]} still needs ' +
            ', '.join(f'{n["name"]}' + (' (signer)' if n['signer'] else '')
                      for n in needed) +
            ' — pass them in accounts as {"name": "<address>"}, or as '
            '{"name": {"seeds": [...]}} to derive one', status=400,
            detail={'missing': needed, 'resolved': resolved})
    return metas, names, hows, resolved


def _is_caller(name):
    """Account names that conventionally mean 'whoever is signing this'."""
    return bool(re.search(r'(^|_)(authority|payer|funder|admin|signer|owner|'
                          r'user|creator|sender)$', name))


def _derive_idl_pda(spec, args, resolved, program, idl):
    """Seeds an anchor IDL declares: constants, argument values, and the keys of
    accounts earlier in the same instruction."""
    blobs = []
    for s in spec.get('seeds') or []:
        kind = s.get('kind')
        if kind == 'const':
            value = s.get('value')
            blobs.append(bytes(value) if isinstance(value, list)
                         else seed_bytes(value))
        elif kind == 'arg':
            path = str(s.get('path') or '').split('.')[0]
            if path not in (args or {}):
                return None
            blobs.append(seed_bytes((args or {})[path]))
        elif kind == 'account':
            path = _snake(str(s.get('path') or '')).lower().split('.')
            if len(path) > 1 or path[0] not in resolved:
                return None                     # needs another account's contents
            blobs.append(b58decode(resolved[path[0]]))
        else:
            return None
    if not blobs:
        return None
    return find_program_address(blobs, spec.get('program') or program)[0]


def invoke(client, program, ix=None, args=None, accounts=None, data=None,
           wallet=None, secret=None, payer=None, send=False, force=False,
           idl=None, decode_after=True):
    """Build one instruction against a program, simulate it, and send it if asked.

    Simulation comes first either way: a call that would fail is reported as the
    reason it fails instead of costing a fee to find out.
    """
    program = need_address(SAMPLES.get(str(program).lower(), program), 'program')
    me, seeds = None, {}
    try:
        seed, me = signer(wallet, secret)
        seeds[me] = seed
    except SolError:
        if send:
            raise
    doc = idl
    if isinstance(doc, str):
        doc = json.loads(doc)
    if doc is None and ix:
        doc, _src = load_idl(client, program)
        if not doc:
            raise SolError(
                f'{program} publishes no IDL, so {ix!r} means nothing here — pass '
                'data= with the raw instruction bytes, or POST the IDL to '
                '/idl and it will be used from then on', status=400)

    ix_def = None
    if ix:
        blob, ix_def = instruction_data(doc, ix, args)
    else:
        blob = _bytes_of(data) if data not in (None, '') else b''

    if ix_def and (accounts is None or isinstance(accounts, dict)):
        metas, names, hows, resolved = _idl_accounts(ix_def, accounts, me,
                                                     program, args, doc)
    else:
        metas = [_resolve_account(a, me, program, args, doc, {})
                 for a in (accounts or [])]
        names, hows, resolved = [None] * len(metas), ['you'] * len(metas), {}

    fee_payer = payer or me or next((m[0] for m in metas if m[1]), None)
    if not fee_payer:
        raise SolError('somebody has to pay the fee — pass wallet= (a keystore '
                       'name), or payer= with an address to simulate as')
    instruction = (program, metas, blob)
    writable = [m[0] for m in metas if m[2]]
    out = {'network': client.network, 'program': program,
           'instruction': ix_def['name'] if ix_def else 'raw',
           'discriminator': binascii.hexlify(blob[:8]).decode() if ix_def else None,
           'data_hex': binascii.hexlify(blob).decode(), 'data_bytes': len(blob),
           'payer': fee_payer,
           'accounts': [{'name': n, 'pubkey': p, 'signer': s, 'writable': w,
                         'via': h} for (p, s, w), n, h in zip(metas, names, hows)],
           'idl_backed': bool(ix_def)}

    sim = client.simulate_ix(fee_payer, [instruction],
                             accounts=writable[:8] if decode_after else None)
    if sim.get('error') and doc:
        sim['reason'] = _explain(sim.get('error'), doc) or sim.get('reason')
    out['simulation'] = {k: v for k, v in sim.items() if k != 'accounts'}
    if decode_after and sim.get('accounts'):
        out['after'] = _decoded_accounts(writable, sim['accounts'], doc)
    if not send:
        out['sent'] = False
        out['note'] = ('simulated only — nothing was signed. Add send=true to '
                       'run it for real.' if sim['ok'] else
                       'simulated only, and it would have failed.')
        # Worth saying plainly: a simulation runs with signature checking off,
        # so an instruction can pass here on a signer whose key nobody holds.
        # Sending is where that stops being true.
        if any(m[1] for m in metas):
            out['note'] += (' Signatures are not checked in simulation, so a '
                            'signer you do not hold the key for still passes.')
        return out
    if not sim['ok'] and not force:
        out['sent'] = False
        out['refused'] = ('the simulation failed, so this was not sent — '
                          'force=true overrides that and pays the fee anyway')
        return out
    missing = [p for p, is_signer, _ in metas if is_signer and p not in seeds]
    if missing:
        raise SolError('this instruction needs signatures from ' +
                       ', '.join(missing) + ' and the keystore has no key for '
                       'them', status=400)
    sent = client.send_ix(fee_payer, [instruction], seeds, wait=True)
    out.update({'sent': True, **sent})
    return out


def _decoded_accounts(addresses, states, idl):
    rows = []
    for address, state in zip(addresses, states or []):
        if not state:
            rows.append({'address': address, 'exists': False})
            continue
        raw = base64.b64decode((state.get('data') or [''])[0])
        row = {'address': address, 'owner': state.get('owner'),
               'sol': sol(state.get('lamports')), 'bytes': len(raw)}
        if idl and raw:
            try:
                hit = decode_account(idl, raw)
                if hit:
                    row.update(hit)
            except SolError as e:
                row['decode_error'] = str(e)
        rows.append(row)
    return rows


def _explain(err, idl):
    """An anchor program's custom error code, in the words the IDL gives it."""
    code = None
    if isinstance(err, dict) and 'InstructionError' in err:
        detail = err['InstructionError'][1]
        if isinstance(detail, dict) and 'Custom' in detail:
            code = detail['Custom']
    if code is None:
        return None
    for e in idl.get('errors') or []:
        if e.get('code') == code:
            return f'{e.get("name")}: {e.get("msg") or "no message in the IDL"} ' \
                   f'(custom error {code})'
    return None
