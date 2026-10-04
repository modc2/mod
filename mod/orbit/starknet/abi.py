"""
starknet/abi.py — read any contract by its own ABI, python stdlib only.

Starknet keeps every contract's class (Sierra + ABI) on chain, so a contract
fully describes itself: fetch the class, and every function can be called by
name with typed arguments, every result decoded back into structs, enums,
u256s, arrays and strings, every event unpacked into named fields.

    iface(address)                     functions + events, short signatures
    read(address, fn, args)            ABI-encode args, starknet_call, decode
    encode(address, fn, args)          calldata only (for a write or a proof)
    events(address, name=, limit=)     newest events, decoded

Classes are immutable per class hash, so their ABIs are cached on disk under
~/.mod/starknet/abi/ forever; the address → class hash hop is re-read live
(contracts can be upgraded with replace_class).
"""

import json
import os
import re

import chain

P = 2 ** 251 + 17 * 2 ** 192 + 1          # the Stark field prime
CACHE = os.path.expanduser(os.environ.get('STARKNET_ABI_CACHE',
                                          '~/.mod/starknet/abi'))

_FELTS = {'felt252', 'felt', 'ContractAddress', 'ClassHash', 'EthAddress',
          'StorageAddress', 'bytes31', 'StorageBaseAddress'}
_UINTS = {'u8', 'u16', 'u32', 'u64', 'u128', 'usize'}
_INTS = {'i8', 'i16', 'i32', 'i64', 'i128'}
_mem = {}   # class_hash -> abi list


class AbiError(ValueError):
    pass


# ---------------------------------------------------------------- types

def short(t):
    """core::array::Span::<privacy::objects::Note> -> Span<Note>"""
    return re.sub(r'[A-Za-z_0-9]+::', '', t.replace('::<', '<'))


def _split(s):
    """Split on top-level commas: 'a, b<c, d>, (e, f)' -> 3 parts."""
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '<(':
            depth += 1
        elif ch in '>)':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _generic(t):
    """'core::array::Span::<X>' -> ('core::array::Span', ['X']) or (t, [])."""
    i = t.find('<')
    if i < 0 or not t.endswith('>'):
        return t, []
    base = t[:i].rstrip(':')
    return base, _split(t[i + 1:-1])


def _leaf(t):
    return t.rsplit('::', 1)[-1]


def _int(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if s.lower().startswith('0x'):
        return int(s, 16)
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    raise AbiError(f'not a number: {v!r}')


def _felt_in(v):
    """felt from int / hex / decimal / short string (<= 31 ascii chars)."""
    try:
        return _int(v) % P
    except AbiError:
        b = str(v).encode()
        if len(b) > 31:
            raise AbiError(f'{v!r} is not a number and too long for a '
                           f'short string (31 bytes max)')
        return int.from_bytes(b, 'big')


def short_string(felt):
    """Decode a felt as a Cairo short string when it is printable ascii."""
    n = _int(felt)
    if n == 0:
        return ''
    b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return b.decode() if all(32 <= c < 127 for c in b) else None


# ---------------------------------------------------------------- codec

class Codec:
    """Encode / decode Cairo values against one contract's ABI."""

    def __init__(self, abi):
        self.abi = abi
        self.structs, self.enums, self.fns, self.events = {}, {}, {}, {}
        self.legacy = any(i.get('type') == 'function' and
                          any(x.get('type') == 'felt' for x in
                              i.get('inputs', []) + i.get('outputs', []))
                          for i in abi)
        for item in abi:
            kind = item.get('type')
            if kind == 'struct':
                self.structs[item['name']] = item['members']
            elif kind == 'enum':
                self.enums[item['name']] = item['variants']
            elif kind in ('function', 'l1_handler'):
                self.fns[item['name']] = {**item, 'interface': None}
            elif kind == 'interface':
                for f in item.get('items', []):
                    if f.get('type') == 'function':
                        self.fns[f['name']] = {**f, 'interface': item['name']}
            elif kind == 'event':
                self.events[item['name']] = item

    # -- decode ------------------------------------------------------

    def decode(self, t, felts, i=0):
        """Decode one value of type t from felts[i:]. Returns (value, next_i)."""
        t = t.strip()
        if t == '()':
            return None, i
        if t.startswith('(') and t.endswith(')'):
            out = []
            for part in _split(t[1:-1]):
                v, i = self.decode(part, felts, i)
                out.append(v)
            return out, i
        leaf = _leaf(t)
        base, args = _generic(t)
        if leaf in _FELTS:
            return hex(_int(felts[i])), i + 1
        if leaf in _UINTS:
            return _int(felts[i]), i + 1
        if leaf in _INTS:
            n = _int(felts[i])
            return (n - P if n > P // 2 else n), i + 1
        if leaf == 'bool':
            return _int(felts[i]) != 0, i + 1
        if t == 'core::integer::u256' or leaf == 'Uint256':
            return _int(felts[i]) + (_int(felts[i + 1]) << 128), i + 2
        if t == 'core::byte_array::ByteArray':
            n = _int(felts[i])
            i += 1
            raw = b''
            for _ in range(n):
                raw += _int(felts[i]).to_bytes(31, 'big')
                i += 1
            word, wlen = _int(felts[i]), _int(felts[i + 1])
            raw += word.to_bytes(wlen, 'big') if wlen else b''
            return raw.decode('utf-8', 'replace'), i + 2
        if _leaf(base) in ('Array', 'Span') and args:
            n = _int(felts[i])
            i += 1
            out = []
            for _ in range(n):
                v, i = self.decode(args[0], felts, i)
                out.append(v)
            return out, i
        if _leaf(base) == 'NonZero' and args:
            return self.decode(args[0], felts, i)
        if t in self.structs:
            if [m['name'] for m in self.structs[t]] == ['low', 'high']:
                return _int(felts[i]) + (_int(felts[i + 1]) << 128), i + 2
            out = {}
            for m in self.structs[t]:
                out[m['name']], i = self.decode(m['type'], felts, i)
            return out, i
        if t in self.enums:
            variants = self.enums[t]
            k = _int(felts[i])
            if k >= len(variants):
                raise AbiError(f'{short(t)}: variant {k} out of range')
            v = variants[k]
            payload, i = self.decode(v['type'], felts, i + 1)
            if t.startswith('core::option::Option'):
                return (payload if v['name'] == 'Some' else None), i
            return ({v['name']: payload} if payload is not None
                    else v['name']), i
        # Unknown / opaque type: hand back one raw felt rather than guessing.
        return hex(_int(felts[i])), i + 1

    def decode_list(self, members, felts):
        """Decode an outputs/inputs list. Legacy `x_len` + `x: felt*` pairs."""
        out, i, last = {}, 0, None
        for m in members:
            t = m['type']
            if t.endswith('*'):
                n = _int(last or 0)
                vals = []
                for _ in range(n):
                    v, i = self.decode(t[:-1], felts, i)
                    vals.append(v)
                out[m.get('name') or f'_{len(out)}'] = vals
            else:
                v, i = self.decode(t, felts, i)
                out[m.get('name') or f'_{len(out)}'] = v
                last = v
        return out, i

    # -- encode ------------------------------------------------------

    def encode(self, t, v):
        t = t.strip()
        if t == '()':
            return []
        if t.startswith('(') and t.endswith(')'):
            parts = _split(t[1:-1])
            v = _jsonish(v)
            if not isinstance(v, (list, tuple)) or len(v) != len(parts):
                raise AbiError(f'{short(t)} wants a list of {len(parts)}')
            return [x for p, e in zip(parts, v) for x in self.encode(p, e)]
        leaf = _leaf(t)
        base, args = _generic(t)
        if leaf == 'ContractAddress' and isinstance(v, str) and \
                not v.lower().startswith('0x'):
            v = resolve(v)
        if leaf in _FELTS:
            return [_felt_in(v)]
        if leaf in _UINTS or leaf in _INTS:
            return [_int(v) % P]
        if leaf == 'bool':
            return [1 if v in (True, 1, '1', 'true', 'True') else 0]
        if t == 'core::integer::u256' or leaf == 'Uint256' or (
                t in self.structs and
                [m['name'] for m in self.structs[t]] == ['low', 'high']):
            n = _int(v)
            return [n & ((1 << 128) - 1), n >> 128]
        if t == 'core::byte_array::ByteArray':
            b = str(v).encode()
            full = [int.from_bytes(b[k:k + 31], 'big')
                    for k in range(0, len(b) - len(b) % 31, 31)]
            rest = b[len(full) * 31:]
            return [len(full), *full, int.from_bytes(rest, 'big') if rest else 0,
                    len(rest)]
        if _leaf(base) in ('Array', 'Span') and args:
            v = _jsonish(v)
            if not isinstance(v, (list, tuple)):
                raise AbiError(f'{short(t)} wants a list')
            return [len(v)] + [x for e in v for x in self.encode(args[0], e)]
        if _leaf(base) == 'NonZero' and args:
            return self.encode(args[0], v)
        if t in self.structs:
            members = self.structs[t]
            v = _jsonish(v)
            if isinstance(v, (list, tuple)):
                v = dict(zip([m['name'] for m in members], v))
            if not isinstance(v, dict):
                raise AbiError(f'{short(t)} wants an object with '
                               f'{[m["name"] for m in members]}')
            missing = [m['name'] for m in members if m['name'] not in v]
            if missing:
                raise AbiError(f'{short(t)} missing {missing}')
            return [x for m in members for x in self.encode(m['type'], v[m['name']])]
        if t in self.enums:
            variants = self.enums[t]
            names = [x['name'] for x in variants]
            v = _jsonish(v)
            if t.startswith('core::option::Option') and not (
                    isinstance(v, dict) and set(v) <= {'Some', 'None'}) and \
                    v not in ('None', 'Some'):
                v = 'None' if v is None else {'Some': v}
            if isinstance(v, str):
                name, payload = v, None
            elif isinstance(v, dict) and len(v) == 1:
                name, payload = next(iter(v.items()))
            else:
                raise AbiError(f'{short(t)} wants "Variant" or '
                               f'{{"Variant": payload}} — one of {names}')
            if name not in names:
                raise AbiError(f'{short(t)} has no variant {name!r}: {names}')
            k = names.index(name)
            return [k] + self.encode(variants[k]['type'], payload)
        return [_felt_in(v)]

    def encode_inputs(self, fn, args):
        f = self.function(fn)
        inputs = f.get('inputs', [])
        args = _jsonish(args)
        if args is None:
            args = []
        if isinstance(args, dict):
            vals = []
            for inp in inputs:
                n = inp['name']
                if n in args:
                    vals.append(args[n])
                elif self.legacy and n.endswith('_len'):
                    vals.append(None)   # filled from the array that follows
                else:
                    raise AbiError(f'{fn} missing argument {n!r} '
                                   f'({short(inp["type"])})')
        else:
            vals = list(args)
            if len(vals) != len(inputs):
                raise AbiError(f'{fn} takes {len(inputs)} args: '
                               f'{signature(f)}')
        out = []
        for k, (inp, v) in enumerate(zip(inputs, vals)):
            t = inp['type']
            if t.endswith('*'):
                v = _jsonish(v) or []
                if vals[k - 1] is None:
                    out.append(len(v))
                out += [x for e in v for x in self.encode(t[:-1], e)]
            elif v is None and self.legacy and inp['name'].endswith('_len'):
                continue
            else:
                out += self.encode(t, v)
        return out

    def function(self, fn):
        if fn not in self.fns:
            raise AbiError(f'no function {fn!r} — has: '
                           f'{", ".join(sorted(self.fns)) or "(none)"}')
        return self.fns[fn]

    def decode_outputs(self, fn, felts):
        f = self.function(fn)
        outs = f.get('outputs', [])
        if self.legacy:
            return self.decode_list(outs, felts)[0]
        if not outs:
            return None
        if len(outs) == 1:
            return self.decode(outs[0]['type'], felts)[0]
        return [self.decode(o['type'], felts)[0] for o in outs]

    # -- events ------------------------------------------------------

    def event_index(self):
        """selector -> event name, for every event struct and enum variant."""
        idx = {}
        for name, ev in self.events.items():
            idx.setdefault(chain.selector(_leaf(name)), name)
            for v in ev.get('variants', []):
                target = v['type'] if v['type'] in self.events else name
                idx.setdefault(chain.selector(v['name']), target)
        return idx

    def decode_event(self, ev, idx=None):
        idx = idx or self.event_index()
        keys, data = list(ev.get('keys', [])), list(ev.get('data', []))
        name = idx.get(chain.felt(keys[0])) if keys else None
        k = 1
        # A nested component event: outer variant selector, then inner.
        while name and self.events[name].get('kind') == 'enum' and k < len(keys):
            inner = {chain.selector(v['name']): v['type']
                     for v in self.events[name].get('variants', [])}
            nxt = inner.get(chain.felt(keys[k]))
            if not nxt or nxt not in self.events:
                break
            name, k = nxt, k + 1
        out = {'event': _leaf(name) if name else None,
               'block_number': ev.get('block_number'),
               'tx': ev.get('transaction_hash')}
        if not name:
            out.update(keys=keys, data=data)
            return out
        spec = self.events[name]
        fields = {}
        try:
            if 'members' in spec:                   # Cairo 1 event struct
                ki, di = k, 0
                for m in spec['members']:
                    if m.get('kind') == 'key':
                        fields[m['name']], ki = self.decode(m['type'], keys, ki)
                    else:
                        fields[m['name']], di = self.decode(m['type'], data, di)
            else:                                   # Cairo 0 event
                fields = self.decode_list(spec.get('data', []), data)[0]
        except (IndexError, AbiError) as e:
            fields = {'_undecoded': str(e), 'keys': keys, 'data': data}
        out['fields'] = fields
        return out


def _jsonish(v):
    """Accept JSON text where a structured value is expected."""
    if isinstance(v, str) and v.strip()[:1] in '[{':
        try:
            return json.loads(v)
        except ValueError:
            pass
    return v


def signature(f):
    ins = ', '.join(f'{i["name"]}: {short(i["type"])}' for i in f.get('inputs', []))
    outs = [short(o['type']) for o in f.get('outputs', [])]
    ret = f' -> {", ".join(outs)}' if outs else ''
    return f'{f["name"]}({ins}){ret}'


# ---------------------------------------------------------------- classes

def class_abi(class_hash, network=None):
    """ABI of a declared class, cached on disk by class hash (immutable)."""
    class_hash = chain.felt(class_hash)
    if class_hash in _mem:
        return _mem[class_hash]
    path = os.path.join(CACHE, f'{class_hash}.json')
    if os.path.exists(path):
        with open(path) as f:
            _mem[class_hash] = json.load(f)
        return _mem[class_hash]
    cls = chain.rpc('starknet_getClass', ['latest', class_hash], network=network)
    abi = cls.get('abi') or []
    if isinstance(abi, str):
        abi = json.loads(abi) if abi.strip() else []
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(abi, f)
    except OSError:
        pass
    _mem[class_hash] = abi
    return abi


def resolve(address, network=None):
    """Aliases: eth / strk / usdc (token contracts), strk20 / pool."""
    a = str(address).strip().lower()
    if a in chain.TOKENS:
        return chain.TOKENS[a]['address']
    if a in ('strk20', 'pool', 'strk20_pool'):
        import strk20
        return strk20.pool(network)
    return address


def codec(address, network=None):
    address = resolve(address, network)
    ch = chain.class_hash(address, network=network)
    return Codec(class_abi(ch, network=network)), ch


def iface(address, network=None):
    """What a contract is: its class hash, functions (view vs external),
    events and whether it is a privacy_invoke helper."""
    address = resolve(address, network)
    c, ch = codec(address, network=network)
    fns = []
    for name, f in c.fns.items():
        fns.append({'name': name,
                    'mutability': f.get('state_mutability') or
                    ('view' if f.get('stateMutability') == 'view' else 'external'),
                    'signature': signature(f),
                    'interface': short(f['interface']) if f.get('interface') else None,
                    'selector': chain.selector(name)})
    events = sorted({_leaf(n) for n, e in c.events.items()
                     if e.get('kind') == 'struct' or 'data' in e})
    return {'address': chain.felt(address), 'class_hash': ch,
            'cairo': 0 if c.legacy else 1,
            'functions': fns,
            'views': [f['name'] for f in fns if f['mutability'] == 'view'],
            'externals': [f['name'] for f in fns if f['mutability'] != 'view'],
            'events': events,
            'privacy_invoke': 'privacy_invoke' in c.fns}


def encode(address, fn, args=None, network=None):
    address = resolve(address, network)
    c, _ = codec(address, network=network)
    calldata = c.encode_inputs(fn, args)
    return {'contract': chain.felt(address), 'entrypoint': fn,
            'selector': chain.selector(fn),
            'signature': signature(c.function(fn)),
            'calldata': [hex(x) for x in calldata]}


def read(address, fn, args=None, block_id='latest', network=None):
    """Call any function by name with typed args; decode the result."""
    address = resolve(address, network)
    c, _ = codec(address, network=network)
    calldata = c.encode_inputs(fn, args)
    raw = chain.call(address, fn, calldata, block_id=block_id, network=network)
    try:
        value = c.decode_outputs(fn, raw)
    except (IndexError, AbiError) as e:
        value = {'_undecoded': str(e)}
    return {'contract': chain.felt(address), 'function': fn,
            'signature': signature(c.function(fn)),
            'result': value, 'raw': raw}


# ---------------------------------------------------------------- events

def fetch_events(address, keys=None, limit=50, from_block=None, to_block=None,
                 network=None, max_span=400_000, chunk=1000, max_pages=20):
    """Newest-first raw events for a contract. With from_block, pages forward
    from there; otherwise walks back from the head in widening windows until
    `limit` events are found or max_span blocks have been searched."""
    address = resolve(address, network)
    addr = chain.felt(address)
    keys = [[chain.felt(k) for k in group] for group in (keys or [])]
    head = chain.block_number(network=network) if to_block in (None, 'latest') \
        else int(to_block)

    def window(lo, hi):
        got, token, pages = [], None, 0
        while pages < max_pages:
            f = {'address': addr, 'from_block': {'block_number': lo},
                 'to_block': {'block_number': hi}, 'keys': keys,
                 'chunk_size': chunk}
            if token:
                f['continuation_token'] = token
            r = chain.rpc('starknet_getEvents', [f], network=network)
            got += r.get('events', [])
            token, pages = r.get('continuation_token'), pages + 1
            if not token:
                break
        return got

    if from_block is not None:
        return {'events': window(int(from_block), head)[:limit],
                'from_block': int(from_block), 'to_block': head}
    out, hi, span, searched = [], head, 2000, 0
    while len(out) < limit and searched < max_span and hi >= 0:
        lo = max(0, hi - span + 1)
        out = window(lo, hi) + out
        searched += hi - lo + 1
        hi, span = lo - 1, span * 4
    out = list(reversed(out))[:limit]
    return {'events': out, 'from_block': max(hi + 1, 0), 'to_block': head,
            'blocks_searched': searched}


def events(address, name=None, limit=20, keys=None, from_block=None,
           network=None):
    """Decoded events, newest first. name filters by event name (its
    selector becomes the first key), keys adds further key filters."""
    address = resolve(address, network)
    c, _ = codec(address, network=network)
    kf = []
    if name:
        kf.append([chain.selector(name)])
    for k in (keys or []):
        kf.append(k if isinstance(k, list) else [k])
    raw = fetch_events(address, kf, limit=int(limit or 20),
                       from_block=from_block, network=network)
    idx = c.event_index()
    return {'contract': chain.felt(address), 'filter': name,
            'from_block': raw['from_block'], 'to_block': raw['to_block'],
            'count': len(raw['events']),
            'events': [c.decode_event(e, idx) for e in raw['events']]}


def selftest():
    """Offline codec vectors."""
    abi = [{'type': 'struct', 'name': 'm::Note',
            'members': [{'name': 'id', 'type': 'core::felt252'},
                        {'name': 'amount', 'type': 'core::integer::u128'}]},
           {'type': 'enum', 'name': 'm::Act',
            'variants': [{'name': 'Nop', 'type': '()'},
                         {'name': 'Pay', 'type': 'm::Note'}]}]
    c = Codec(abi)
    span = 'core::array::Span::<m::Note>'
    felts = c.encode(span, [{'id': '0x5', 'amount': 7}])
    assert felts == [1, 5, 7], felts
    assert c.decode(span, felts)[0] == [{'id': '0x5', 'amount': 7}]
    assert c.encode('m::Act', {'Pay': [9, 1]}) == [1, 9, 1]
    assert c.decode('m::Act', [0])[0] == 'Nop'
    assert c.encode('core::integer::u256', 2 ** 128 + 3) == [3, 1]
    s = 'hello world, this is longer than 31 bytes'
    ba = c.encode('core::byte_array::ByteArray', s)
    assert c.decode('core::byte_array::ByteArray', ba)[0] == s
    assert c.decode('(core::felt252, core::bool)', [10, 1])[0] == ['0xa', True]
    assert short_string(_felt_in('ERC20')) == 'ERC20'
    return {'abi_codec': 'ok'}
