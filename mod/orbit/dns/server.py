"""
The listener — an authoritative name server for the zones this module holds.

`answer()` is the whole resolution policy and it is deliberately readable,
because every subtle DNS bug lives in this function:

  * the deepest zone whose name is a suffix of the question wins; if none is,
    we are not authoritative and say REFUSED rather than lying;
  * an exact name match answers first;
  * CNAME answers a query of any type, and if it points inside the zone the
    target's records are chased and appended — the thing recursive resolvers
    expect and stub resolvers cannot do for themselves;
  * a wildcard only applies when the exact name does not exist at all, which
    is the rule people get wrong (`*.host` must not shadow a name that exists
    with a different type);
  * a name that exists with other types is NODATA — NOERROR, no answers, SOA
    in authority — while a name that does not exist at all is NXDOMAIN. A
    server that returns NXDOMAIN for the first case breaks mail and TLS
    issuance in ways nobody traces back to DNS for days.

Every query is counted and the last few thousand are kept in a ring buffer, so
the console can show resolutions next to changes. Queries are not written to
the operation log: there are millions of them and none of them change anything.
"""
import collections
import socket
import socketserver
import struct
import threading
import time

import fleet
import settings
import wire
import zone as Z

MAX_LOG = int(2000)

_state = {
    'running': False, 'started': None, 'port': None, 'bind': None,
    'udp': None, 'tcp': None, 'threads': [], 'error': None,
}
_queries = collections.deque(maxlen=MAX_LOG)
_counts = {'total': 0, 'by_type': {}, 'by_rcode': {}, 'by_name': {}}
_lock = threading.Lock()


# ── resolution ───────────────────────────────────────────────────────────

def _rrs(zone_name, records, qname, qtype_name):
    out = []
    for r in records:
        if r['fqdn'] != qname:
            continue
        if qtype_name not in ('ANY', r['type']):
            continue
        out.append(wire.RR(qname, r['type'], r['value'], r['ttl']))
    return out


def answer(qname, qtype='A'):
    """Resolve one question against the zones held here. Pure — no sockets."""
    qname = wire.normalize(qname)
    qtype_name = str(qtype).upper()
    if qtype_name not in wire.TYPES:
        return {'rcode': 'NOTIMP', 'answers': [], 'authority': [],
                'question': {'name': qname, 'type': qtype_name},
                'why': f'{qtype_name} is not a type this server serves'}
    z = Z.find_zone(qname)
    if not z:
        return {'rcode': 'REFUSED', 'answers': [], 'authority': [],
                'question': {'name': qname, 'type': qtype_name},
                'zone': None, 'authoritative': False,
                'why': f'not authoritative for {qname} — no zone here covers '
                       f'it. Register the zone to change that.'}
    zn = z['zone']
    all_recs = Z.records(zn)['records']
    names = {r['fqdn'] for r in all_recs}
    soa = [wire.RR(zn, 'SOA', r['value'], r['ttl'])
           for r in all_recs if r['type'] == 'SOA' and r['fqdn'] == zn][:1]

    answers, matched, why = [], None, None
    exact = _rrs(zn, all_recs, qname, qtype_name)
    if exact:
        answers, matched, why = exact, 'exact', 'an exact record for this name'
    else:
        cname = _rrs(zn, all_recs, qname, 'CNAME')
        if cname and qtype_name not in ('CNAME', 'ANY'):
            answers = list(cname)
            matched, why = 'cname', 'the name is an alias; the target was chased'
            target, hops = wire.normalize(cname[0].value), 0
            while hops < 8:
                hops += 1
                nxt = _rrs(zn, all_recs, target, qtype_name)
                if nxt:
                    answers += nxt
                    break
                nxt_cname = _rrs(zn, all_recs, target, 'CNAME')
                if not nxt_cname:
                    break
                answers += nxt_cname
                target = wire.normalize(nxt_cname[0].value)
        elif qname not in names:
            # Wildcards only cover names that do not otherwise exist.
            labels = qname.split('.')
            for i in range(1, len(labels)):
                candidate = '*.' + '.'.join(labels[i:])
                if not (candidate == '*.' + zn or candidate.endswith('.' + zn)):
                    continue
                wild = [r for r in all_recs if r['fqdn'] == candidate
                        and qtype_name in ('ANY', r['type'])]
                if wild:
                    answers = [wire.RR(qname, r['type'], r['value'], r['ttl'])
                               for r in wild]
                    matched, why = 'wildcard', f'matched the wildcard {candidate}'
                    break
                if any(r['fqdn'] == candidate for r in all_recs):
                    break

    if answers:
        return {'rcode': 'NOERROR', 'answers': [rr.as_dict() for rr in answers],
                'authority': [], 'zone': zn, 'authoritative': True,
                'matched': matched, 'why': why,
                'question': {'name': qname, 'type': qtype_name},
                '_rrs': answers, '_soa': soa}
    if qname in names:
        return {'rcode': 'NOERROR', 'answers': [], 'zone': zn,
                'authority': [rr.as_dict() for rr in soa], 'authoritative': True,
                'matched': 'nodata',
                'why': f'{qname} exists here but has no {qtype_name} record — '
                       f'NODATA, not NXDOMAIN',
                'question': {'name': qname, 'type': qtype_name},
                '_rrs': [], '_soa': soa}
    return {'rcode': 'NXDOMAIN', 'answers': [], 'zone': zn,
            'authority': [rr.as_dict() for rr in soa], 'authoritative': True,
            'matched': None, 'why': f'no name {qname} in {zn}',
            'question': {'name': qname, 'type': qtype_name},
            '_rrs': [], '_soa': soa}


def _note(qname, qtype, rcode, client, ms, matched):
    with _lock:
        _counts['total'] += 1
        _counts['by_type'][qtype] = _counts['by_type'].get(qtype, 0) + 1
        _counts['by_rcode'][rcode] = _counts['by_rcode'].get(rcode, 0) + 1
        _counts['by_name'][qname] = _counts['by_name'].get(qname, 0) + 1
        if len(_counts['by_name']) > 5000:
            top = sorted(_counts['by_name'].items(), key=lambda kv: -kv[1])[:1000]
            _counts['by_name'] = dict(top)
        _queries.append({'at': time.time(), 'name': qname, 'type': qtype,
                         'rcode': rcode, 'client': client, 'ms': round(ms, 2),
                         'matched': matched})


def handle_packet(data, client='?'):
    """Bytes in, bytes out. The transport does not matter to resolution."""
    started = time.perf_counter()
    try:
        query = wire.Message.parse(data)
    except wire.WireError:
        return None
    if query.qr or not query.questions:
        reply = wire.build_response(query, rcode=wire.FORMERR, aa=False)
        return reply.encode(wire.MAX_UDP)
    if query.opcode != 0:
        reply = wire.build_response(query, rcode=wire.NOTIMP, aa=False)
        return reply.encode(wire.MAX_UDP)
    q = query.questions[0]
    result = answer(q.name, q.type_name)
    rcode = getattr(wire, result['rcode'], wire.SERVFAIL)
    reply = wire.build_response(
        query, answers=result.get('_rrs') or [],
        authority=(result.get('_soa') or []) if not result.get('_rrs') else [],
        rcode=rcode, aa=result.get('authoritative', False))
    _note(q.name, q.type_name, result['rcode'], client,
          (time.perf_counter() - started) * 1000, result.get('matched'))
    limit = query.edns or wire.MAX_UDP
    return reply.encode(limit)


class _UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        out = handle_packet(data, self.client_address[0])
        if out:
            sock.sendto(out, self.client_address)


class _TCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            head = self.request.recv(2)
            if len(head) < 2:
                return
            length = struct.unpack('!H', head)[0]
            data = b''
            while len(data) < length:
                chunk = self.request.recv(length - len(data))
                if not chunk:
                    return
                data += chunk
            out = handle_packet(data, self.client_address[0])
            if out:
                self.request.sendall(struct.pack('!H', len(out)) + out)
        except (OSError, struct.error):
            return


class _UDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True


class _TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(port=None, bind=None):
    """Bind UDP and TCP. Owner-only upstream; this function just binds."""
    if _state['running']:
        return dict(state(), already=True)
    port = int(port or settings.get('dns_port', 15353))
    bind = bind or settings.get('bind', '0.0.0.0')
    # The attribution records name the key this box signs its module cards
    # with, and finding it imports the protocol. Do it here, off the query
    # path, so no resolver ever waits for it.
    threading.Thread(target=fleet.box_key, daemon=True, name='dns-key').start()
    try:
        udp = _UDPServer((bind, port), _UDP)
        try:
            tcp = _TCPServer((bind, port), _TCP)
        except OSError:
            udp.server_close()
            raise
    except OSError as e:
        _state['error'] = (
            f'cannot bind {bind}:{port} — {e}. '
            + ('Port 53 needs root and conflicts with systemd-resolved on many '
               'boxes; 15353 is the unprivileged default and a resolver can be '
               'pointed at it explicitly.' if port == 53 else
               'Something else is on that port.'))
        return dict(state(), error=_state['error'])
    threads = [threading.Thread(target=udp.serve_forever, daemon=True,
                                name='dns-udp'),
               threading.Thread(target=tcp.serve_forever, daemon=True,
                                name='dns-tcp')]
    for t in threads:
        t.start()
    _state.update(running=True, started=time.time(), port=port, bind=bind,
                  udp=udp, tcp=tcp, threads=threads, error=None)
    return state()


def stop():
    if not _state['running']:
        return dict(state(), already=True)
    for key in ('udp', 'tcp'):
        srv = _state.get(key)
        if srv:
            srv.shutdown()
            srv.server_close()
    _state.update(running=False, udp=None, tcp=None, threads=[], started=None)
    return state()


def state():
    zones = Z.zones()
    return {
        'running': _state['running'],
        'port': _state['port'] or settings.get('dns_port'),
        'bind': _state['bind'] or settings.get('bind'),
        'transports': ['udp', 'tcp'] if _state['running'] else [],
        'uptime_s': int(time.time() - _state['started']) if _state['started'] else 0,
        'error': _state['error'],
        'zones': [z['zone'] for z in zones],
        'queries': dict(_counts, by_name=dict(sorted(
            _counts['by_name'].items(), key=lambda kv: -kv[1])[:20])),
        'try_it': (f'dig @<box> -p {_state["port"] or settings.get("dns_port")} '
                   f'{settings.host()} A' if _state['running'] else
                   'not listening — start it with serve()'),
    }


def recent(limit=100, name=None, rcode=None):
    """The last few thousand questions this server was asked."""
    out = []
    for q in reversed(_queries):
        if name and name.lower() not in q['name']:
            continue
        if rcode and q['rcode'] != rcode.upper():
            continue
        out.append(q)
        if len(out) >= int(limit):
            break
    return {'queries': out, 'kept': len(_queries), 'capacity': MAX_LOG,
            'counts': _counts}
