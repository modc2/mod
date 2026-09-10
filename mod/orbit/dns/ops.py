"""
The operations — the catalog, and the log.

Two halves of one idea. The **catalog** is the closed set of things this module
can do to the protocol's names, each one carrying the standing it requires, so
"who may change what" is data you can read rather than a rule buried in a
handler. The console renders it, `GET /operations` returns it, and the MCP
tool `dns_operations` hands it to an agent. Nothing mutates outside it.

The **log** is what actually happened: an append-only JSONL of every change,
with the operation, the address that made it, the standing that let them, the
zone and record it touched, and the before/after. DNS breaks in ways that are
invisible until a resolver somewhere returns the wrong address, so the useful
question is almost never "what does the zone say" — it is "what changed, when,
and who did it". That file answers it.

Queries are logged too, but not here: they land in a bounded ring buffer in
`server.py`, because there are millions of them and none of them change
anything. The console shows both — changes on the left, resolutions on the
right — under the one word the user cares about: operations.
"""
import json
import os
import threading
import time
from pathlib import Path

STATE = Path(os.path.expanduser(os.environ.get('DNS_DIR', '~/.mod/dns')))
LOG = STATE / 'ops.jsonl'
MAX_BYTES = int(os.environ.get('DNS_OPS_MAX_BYTES', 8 * 1024 * 1024))

_lock = threading.Lock()

# ── the catalog ──────────────────────────────────────────────────────────
# who: anon (open) | holder (any signed caller) | zone_owner | owner
CATALOG = [
    # reading the protocol's names
    ('resolve', 'anon', False, 'Resolve a mod name',
     'Turn a module name, a hostname or a gateway URL into the addresses that '
     'serve it: app, API and MCP URLs, the upstream ports, whether they are '
     'live, and the DNS records that make the hostname answer.'),
    ('lookup', 'anon', False, 'Query this server',
     'Ask this name server a question the way a resolver would, and see the '
     'answer it would put on the wire — including NXDOMAIN and wildcards.'),
    ('check', 'anon', False, 'Compare against the public internet',
     'Ask a public resolver the same question and diff its answer against the '
     'record held here. This is how you learn a host is not actually pointed '
     'at this box, or is proxied by somebody else.'),
    ('zones', 'anon', False, 'List zones', 'Every zone served here, who owns '
     'it, and whether it is the system zone.'),
    ('records', 'anon', False, 'List records', 'The merged record set of a '
     'zone: what is stored, and what the protocol derives from the fleet.'),
    ('operations', 'anon', False, 'This catalog', 'Every operation, and the '
     'standing it requires.'),
    ('ops', 'anon', False, 'The change log', 'Every change ever made, with the '
     'address that made it.'),
    ('attribution', 'anon', False, 'Who a name belongs to',
     'What the mod protocol attributes a module to — the owner address it '
     'declares, its schema CID, and the key this box signs its module card '
     'with — together with the _mod TXT record that publishes all three beside '
     'the address the name resolves to.'),
    ('stats', 'anon', False, 'Server stats', 'Zones, records, queries by type '
     'and rcode, listener state, uptime.'),

    # anyone signed — your own host, your own names
    ('zone_register', 'holder', True, 'Register a host',
     'Claim a domain you control and become the owner of its zone here. This '
     'is the path to running the protocol on a host other than the system '
     'one: it needs no permission from the deployment owner, and the '
     'registrant — not the owner — controls every record in it.'),
    ('record_set', 'zone_owner', True, 'Add or update a record',
     'Write one record into a zone you own. Values are validated by encoding '
     'them to wire format, so a record that cannot be served is refused at '
     'write time rather than at query time.'),
    ('record_delete', 'zone_owner', True, 'Delete a record',
     'Remove one record from a zone you own.'),
    ('zone_target', 'zone_owner', True, 'Point a host at a box',
     'Set the address a zone resolves to. Rewrites the apex, the wildcard and '
     'the per-module names in one operation.'),
    ('zone_verify', 'holder', True, 'Prove a zone is yours',
     'Look for the challenge TXT record in the public DNS. Registering a zone '
     'here claims it; only delegation from the registrar proves it, and this '
     'is what checks.'),
    ('zone_delete', 'zone_owner', True, 'Delete a zone',
     'Drop a zone you own. The system zone cannot be deleted at all.'),

    # owner only — the key system changes
    ('host_set', 'owner', True, 'Repoint the protocol host',
     'Change the host the mod protocol answers on — modc2.com by default. '
     'Every derived name, every resolver answer and the router follow it, so '
     'this is the single most consequential setting here and it is the '
     "owner's alone."),
    ('settings_set', 'owner', True, 'Change system settings',
     'The system target address, default TTL, SOA and NS values, and whether '
     'per-module names and the wildcard are derived at all.'),
    ('system_record', 'owner', True, 'Change a system record',
     'Write or pin a record in the system zone, including the apex, the '
     'wildcard and the nameserver set. Derived records are protocol state, '
     'not user data.'),
    ('serve', 'owner', True, 'Start the name server',
     'Bind the authoritative listener (UDP and TCP). Binding a port is a '
     'system change even when the records are not.'),
    ('kill', 'owner', True, 'Stop the name server', 'Release the listener.'),
    ('router_sync', 'owner', True, 'Sync the router',
     'Hand the host to the caddy module so HTTP routing and DNS agree on what '
     'the protocol answers on. Touching the live router is owner-only.'),
    ('ops_prune', 'owner', True, 'Trim the change log',
     'Roll the log file. The only operation that can remove evidence, so it '
     'writes its own entry first.'),
]

OPERATIONS = [
    {'id': i, 'who': who, 'mutates': mut, 'title': title, 'what': what}
    for i, who, mut, title, what in CATALOG
]
BY_ID = {o['id']: o for o in OPERATIONS}

WHO_MEANS = {
    'anon': 'anyone, signed in or not',
    'holder': 'any signed caller',
    'zone_owner': 'the address that registered the zone (or the deployment owner)',
    'owner': 'the deployment owner only',
}


def catalog(who=None):
    ops = [dict(o, who_means=WHO_MEANS[o['who']]) for o in OPERATIONS]
    if who:
        ops = [o for o in ops if o['who'] == who]
    return {'operations': ops,
            'standings': WHO_MEANS,
            'rule': 'reads are open to everyone; every change is attributed to '
                    'a signed address; the system zone and the protocol host '
                    'belong to the deployment owner, and every other host '
                    'belongs to whoever registered it'}


# ── the log ──────────────────────────────────────────────────────────────

def record(op, actor=None, role='anon', zone=None, target=None,
           before=None, after=None, detail=None, ok=True):
    """Append one operation. Never raises into a caller's request path."""
    entry = {
        'at': int(time.time()),
        'op': op,
        'actor': actor,
        'role': role,
        'zone': zone,
        'target': target,
        'before': before,
        'after': after,
        'detail': detail,
        'ok': ok,
    }
    try:
        with _lock:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            if LOG.exists() and LOG.stat().st_size > MAX_BYTES:
                LOG.rename(LOG.with_suffix('.jsonl.1'))
            with LOG.open('a') as f:
                f.write(json.dumps(entry, default=str) + '\n')
    except OSError:
        pass
    return entry


def log(limit=100, op=None, zone=None, actor=None, since=None):
    """The change log, newest first."""
    try:
        lines = LOG.read_text().splitlines()
    except OSError:
        return {'ops': [], 'total': 0, 'path': str(LOG)}
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if op and e.get('op') != op:
            continue
        if zone and e.get('zone') != zone:
            continue
        if actor and (e.get('actor') or '').lower() != actor.lower():
            continue
        if since and e.get('at', 0) < int(since):
            continue
        out.append(e)
        if len(out) >= int(limit):
            break
    return {'ops': out, 'total': len(lines), 'path': str(LOG),
            'note': 'newest first; every mutation in this module lands here'}


def summary():
    """Who has been changing what — the log folded into counts."""
    try:
        lines = LOG.read_text().splitlines()
    except OSError:
        return {'total': 0, 'by_op': {}, 'by_actor': {}, 'last': None}
    by_op, by_actor, last = {}, {}, None
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        by_op[e.get('op')] = by_op.get(e.get('op'), 0) + 1
        a = e.get('actor') or 'anon'
        by_actor[a] = by_actor.get(a, 0) + 1
        last = e
    return {'total': len(lines), 'by_op': by_op, 'by_actor': by_actor,
            'last': last}


def prune(keep=200, actor=None):
    """Roll the log, keeping the newest `keep` entries. Logs itself first."""
    record('ops_prune', actor=actor, role='owner', detail=f'keep={keep}')
    try:
        lines = LOG.read_text().splitlines()
    except OSError:
        return {'kept': 0, 'dropped': 0}
    keep = int(keep)
    kept = lines[-keep:] if keep else []
    with _lock:
        LOG.with_suffix('.jsonl.1').write_text('\n'.join(lines) + '\n')
        LOG.write_text('\n'.join(kept) + ('\n' if kept else ''))
    return {'kept': len(kept), 'dropped': len(lines) - len(kept),
            'archive': str(LOG.with_suffix('.jsonl.1'))}
