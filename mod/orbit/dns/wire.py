"""
DNS on the wire — RFC 1035 message codec, in the standard library.

This is the layer that makes the module an actual name server rather than a
table of records with a REST API in front of it: a resolver on the internet
speaks 12-byte headers and length-prefixed labels, so we do too.

Encoding is deliberately plain. Names in the question and in owner fields are
written with compression pointers (they repeat constantly and 512 bytes is not
much); names *inside* rdata are written uncompressed, which is always legal and
removes the class of bug where a pointer is computed against the wrong offset.

Decoding is defensive, because the input is a UDP packet from a stranger:
pointer chains are bounded, lengths are checked against the buffer, and a
malformed message raises `WireError` rather than looping.

The same codec is used in both directions — `server.py` answers with it, and
`query()` here uses it to ASK a public resolver what the world currently
believes, which is how `check()` compares the record we hold against the
record the internet actually returns.
"""
import random
import socket
import struct

# ── record types we speak ────────────────────────────────────────────────
TYPES = {
    'A': 1, 'NS': 2, 'CNAME': 5, 'SOA': 6, 'PTR': 12, 'MX': 15, 'TXT': 16,
    'AAAA': 28, 'SRV': 33, 'OPT': 41, 'CAA': 257, 'ANY': 255,
}
TYPE_NAMES = {v: k for k, v in TYPES.items()}

CLASS_IN = 1

# rcodes
NOERROR, FORMERR, SERVFAIL, NXDOMAIN, NOTIMP, REFUSED = 0, 1, 2, 3, 4, 5
RCODE_NAMES = {0: 'NOERROR', 1: 'FORMERR', 2: 'SERVFAIL', 3: 'NXDOMAIN',
               4: 'NOTIMP', 5: 'REFUSED'}

MAX_UDP = 512
EDNS_UDP = 1232


class WireError(Exception):
    """The bytes are not a DNS message we can make sense of."""


# ── names ────────────────────────────────────────────────────────────────

def normalize(name):
    """Lowercase, no trailing dot, no empty labels. '' is the root."""
    n = (name or '').strip().rstrip('.').lower()
    return n


def encode_name(name, buf=None, offsets=None):
    """Wire-encode a domain name, using a compression pointer when the same
    suffix has already been written into `buf`."""
    name = normalize(name)
    labels = [l for l in name.split('.') if l] if name else []
    out = bytearray()
    base = len(buf) if buf is not None else None
    for i in range(len(labels)):
        suffix = '.'.join(labels[i:])
        if offsets is not None and suffix in offsets:
            out += struct.pack('!H', 0xC000 | offsets[suffix])
            return bytes(out)
        if offsets is not None and base is not None:
            here = base + len(out)
            if here < 0x4000:          # only 14 bits of pointer exist
                offsets[suffix] = here
        raw = labels[i].encode('idna') if any(ord(c) > 127 for c in labels[i]) \
            else labels[i].encode('ascii')
        if not 1 <= len(raw) <= 63:
            raise WireError(f'label out of range in {name!r}')
        out += bytes([len(raw)]) + raw
    out += b'\x00'
    if len(out) > 255:
        raise WireError(f'name too long: {name!r}')
    return bytes(out)


def decode_name(data, pos):
    """Return (name, next_pos). Follows compression pointers, bounded."""
    labels, jumps, next_pos = [], 0, None
    while True:
        if pos >= len(data):
            raise WireError('name runs past end of message')
        length = data[pos]
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                raise WireError('truncated compression pointer')
            ptr = struct.unpack('!H', data[pos:pos + 2])[0] & 0x3FFF
            if next_pos is None:
                next_pos = pos + 2
            jumps += 1
            if jumps > 32:
                raise WireError('compression pointer loop')
            if ptr >= len(data):
                raise WireError('compression pointer past end')
            pos = ptr
            continue
        if length & 0xC0:
            raise WireError('reserved label type')
        pos += 1
        if length == 0:
            return '.'.join(labels), (next_pos if next_pos is not None else pos)
        if pos + length > len(data):
            raise WireError('label runs past end of message')
        labels.append(data[pos:pos + length].decode('latin-1').lower())
        pos += length
        if len(labels) > 128:
            raise WireError('too many labels')


# ── rdata ────────────────────────────────────────────────────────────────

def _ipv4(value):
    parts = str(value).strip().split('.')
    if len(parts) != 4:
        raise WireError(f'not an IPv4 address: {value!r}')
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        raise WireError(f'not an IPv4 address: {value!r}')
    if any(o < 0 or o > 255 for o in octets):
        raise WireError(f'not an IPv4 address: {value!r}')
    return bytes(octets)


def _ipv6(value):
    try:
        return socket.inet_pton(socket.AF_INET6, str(value).strip())
    except OSError:
        raise WireError(f'not an IPv6 address: {value!r}')


def _txt(value):
    raw = str(value).encode('utf-8')
    out = bytearray()
    for i in range(0, max(len(raw), 1), 255):
        chunk = raw[i:i + 255]
        out += bytes([len(chunk)]) + chunk
    return bytes(out)


def encode_rdata(rtype, value):
    """Record value (as a human wrote it) → rdata bytes."""
    t = TYPE_NAMES.get(rtype, rtype) if isinstance(rtype, int) else rtype.upper()
    v = value
    if t == 'A':
        return _ipv4(v)
    if t == 'AAAA':
        return _ipv6(v)
    if t in ('CNAME', 'NS', 'PTR'):
        return encode_name(v)
    if t == 'TXT':
        return _txt(v)
    if t == 'MX':
        parts = str(v).split(None, 1)
        if len(parts) == 1:
            pref, host = 10, parts[0]
        else:
            pref, host = int(parts[0]), parts[1]
        return struct.pack('!H', pref) + encode_name(host)
    if t == 'SRV':
        parts = str(v).split()
        if len(parts) != 4:
            raise WireError('SRV value must be "priority weight port target"')
        pri, wt, port, target = int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
        return struct.pack('!HHH', pri, wt, port) + encode_name(target)
    if t == 'CAA':
        parts = str(v).split(None, 2)
        if len(parts) != 3:
            raise WireError('CAA value must be "flags tag value"')
        flags, tag, val = int(parts[0]), parts[1], parts[2].strip('"')
        tagb = tag.encode('ascii')
        return bytes([flags, len(tagb)]) + tagb + val.encode('ascii')
    if t == 'SOA':
        parts = str(v).split()
        if len(parts) != 7:
            raise WireError('SOA value must be "mname rname serial refresh '
                            'retry expire minimum"')
        return (encode_name(parts[0]) + encode_name(parts[1]) +
                struct.pack('!IIIII', *[int(x) for x in parts[2:]]))
    raise WireError(f'unsupported record type: {t}')


def decode_rdata(rtype, data, pos, length):
    """rdata bytes → the same string form encode_rdata accepts."""
    t = TYPE_NAMES.get(rtype, str(rtype))
    end = pos + length
    if end > len(data):
        raise WireError('rdata runs past end of message')
    if t == 'A' and length == 4:
        return '.'.join(str(b) for b in data[pos:end])
    if t == 'AAAA' and length == 16:
        return socket.inet_ntop(socket.AF_INET6, data[pos:end])
    if t in ('CNAME', 'NS', 'PTR'):
        return decode_name(data, pos)[0]
    if t == 'TXT':
        out, p = [], pos
        while p < end:
            n = data[p]
            out.append(data[p + 1:p + 1 + n].decode('utf-8', 'replace'))
            p += 1 + n
        return ''.join(out)
    if t == 'MX':
        pref = struct.unpack('!H', data[pos:pos + 2])[0]
        return f'{pref} {decode_name(data, pos + 2)[0]}'
    if t == 'SRV':
        pri, wt, port = struct.unpack('!HHH', data[pos:pos + 6])
        return f'{pri} {wt} {port} {decode_name(data, pos + 6)[0]}'
    if t == 'CAA':
        flags, taglen = data[pos], data[pos + 1]
        tag = data[pos + 2:pos + 2 + taglen].decode('ascii', 'replace')
        return f'{flags} {tag} {data[pos + 2 + taglen:end].decode("ascii", "replace")}'
    if t == 'SOA':
        mname, p = decode_name(data, pos)
        rname, p = decode_name(data, p)
        nums = struct.unpack('!IIIII', data[p:p + 20])
        return f'{mname} {rname} ' + ' '.join(str(n) for n in nums)
    return data[pos:end].hex()


# ── messages ─────────────────────────────────────────────────────────────

class Question:
    __slots__ = ('name', 'qtype', 'qclass')

    def __init__(self, name, qtype, qclass=CLASS_IN):
        self.name, self.qtype, self.qclass = normalize(name), qtype, qclass

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.qtype, str(self.qtype))

    def as_dict(self):
        return {'name': self.name, 'type': self.type_name}


class RR:
    __slots__ = ('name', 'rtype', 'ttl', 'value', 'rclass')

    def __init__(self, name, rtype, value, ttl=300, rclass=CLASS_IN):
        self.name = normalize(name)
        self.rtype = TYPES[rtype.upper()] if isinstance(rtype, str) else rtype
        self.value, self.ttl, self.rclass = value, int(ttl), rclass

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.rtype, str(self.rtype))

    def as_dict(self):
        return {'name': self.name, 'type': self.type_name,
                'value': self.value, 'ttl': self.ttl}

    def encode(self, buf, offsets):
        out = bytearray(encode_name(self.name, buf, offsets))
        rdata = encode_rdata(self.rtype, self.value)
        out += struct.pack('!HHIH', self.rtype, self.rclass, self.ttl, len(rdata))
        out += rdata
        return bytes(out)


class Message:
    """A parsed DNS message. Only the fields an authoritative server needs."""

    def __init__(self, qid=0, flags=0):
        self.id = qid
        self.flags = flags
        self.questions = []
        self.answers = []
        self.authority = []
        self.additional = []
        self.edns = None          # advertised UDP payload size, or None

    # flag helpers
    @property
    def qr(self):
        return bool(self.flags & 0x8000)

    @property
    def opcode(self):
        return (self.flags >> 11) & 0xF

    @property
    def rd(self):
        return bool(self.flags & 0x0100)

    @property
    def rcode(self):
        return self.flags & 0xF

    @staticmethod
    def parse(data):
        if len(data) < 12:
            raise WireError('message shorter than a header')
        qid, flags, qd, an, ns, ar = struct.unpack('!HHHHHH', data[:12])
        msg = Message(qid, flags)
        pos = 12
        for _ in range(qd):
            name, pos = decode_name(data, pos)
            if pos + 4 > len(data):
                raise WireError('truncated question')
            qtype, qclass = struct.unpack('!HH', data[pos:pos + 4])
            pos += 4
            msg.questions.append(Question(name, qtype, qclass))
        for section, count in ((msg.answers, an), (msg.authority, ns),
                               (msg.additional, ar)):
            for _ in range(count):
                name, pos = decode_name(data, pos)
                if pos + 10 > len(data):
                    raise WireError('truncated record')
                rtype, rclass, ttl, rdlen = struct.unpack('!HHIH', data[pos:pos + 10])
                pos += 10
                if rtype == TYPES['OPT']:
                    msg.edns = rclass       # OPT reuses class as payload size
                    pos += rdlen
                    continue
                try:
                    value = decode_rdata(rtype, data, pos, rdlen)
                except WireError:
                    value = data[pos:pos + rdlen].hex()
                pos += rdlen
                section.append(RR(name, rtype, value, ttl, rclass))
        return msg

    def encode(self, max_size=None):
        """Serialize. Sets TC and drops the answer section if it will not fit."""
        buf = bytearray(struct.pack('!HHHHHH', self.id, self.flags,
                                    len(self.questions), len(self.answers),
                                    len(self.authority), len(self.additional)))
        offsets = {}
        for q in self.questions:
            buf += encode_name(q.name, buf, offsets)
            buf += struct.pack('!HH', q.qtype, q.qclass)
        for section in (self.answers, self.authority, self.additional):
            for rr in section:
                buf += rr.encode(buf, offsets)
        if self.edns is not None:
            buf += b'\x00' + struct.pack('!HHIH', TYPES['OPT'], EDNS_UDP, 0, 0)
            struct.pack_into('!H', buf, 10, len(self.additional) + 1)
        if max_size and len(buf) > max_size:
            truncated = Message(self.id, self.flags | 0x0200)   # TC
            truncated.questions = self.questions
            truncated.edns = self.edns
            return truncated.encode(None)
        return bytes(buf)


def build_response(query, answers=(), authority=(), additional=(),
                   rcode=NOERROR, aa=True, ra=False):
    """An authoritative reply to `query`."""
    flags = 0x8000 | (query.opcode << 11) | rcode
    if aa:
        flags |= 0x0400
    if query.rd:
        flags |= 0x0100
    if ra:
        flags |= 0x0080
    msg = Message(query.id, flags)
    msg.questions = list(query.questions)
    msg.answers = list(answers)
    msg.authority = list(authority)
    msg.additional = list(additional)
    msg.edns = EDNS_UDP if query.edns is not None else None
    return msg


# ── asking somebody else ─────────────────────────────────────────────────

def query(name, rtype='A', server='1.1.1.1', port=53, timeout=3.0, recursive=True):
    """Ask a resolver a question and parse the answer.

    Used by `check()` to compare the record this module holds against what the
    public internet actually returns for the same name.
    """
    qtype = TYPES.get(str(rtype).upper())
    if qtype is None:
        raise WireError(f'unsupported record type: {rtype}')
    msg = Message(random.SystemRandom().randrange(0, 65536),
                  0x0100 if recursive else 0)
    msg.questions.append(Question(name, qtype))
    packet = msg.encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, port))
        while True:
            data, _ = sock.recvfrom(4096)
            reply = Message.parse(data)
            if reply.id == msg.id:
                break
    if reply.flags & 0x0200:          # truncated — retry over TCP
        with socket.create_connection((server, port), timeout=timeout) as tcp:
            tcp.sendall(struct.pack('!H', len(packet)) + packet)
            head = _recv_exact(tcp, 2, timeout)
            body = _recv_exact(tcp, struct.unpack('!H', head)[0], timeout)
            reply = Message.parse(body)
    return {
        'name': normalize(name),
        'type': str(rtype).upper(),
        'server': server,
        'rcode': RCODE_NAMES.get(reply.rcode, reply.rcode),
        'answers': [rr.as_dict() for rr in reply.answers],
        'authority': [rr.as_dict() for rr in reply.authority],
        'values': [rr.value for rr in reply.answers if rr.rtype == qtype],
    }


def _recv_exact(sock, n, timeout):
    sock.settimeout(timeout)
    out = b''
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise WireError('connection closed mid-message')
        out += chunk
    return out
