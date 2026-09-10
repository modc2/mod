"""
The guide — the part of this module that assumes you have never done this.

DNS is a subject that punishes newcomers twice: once with vocabulary (zone,
apex, glue, NODATA) and once with delay, because a mistake is invisible until
some resolver somewhere answers wrong. Every other file here answers a precise
question precisely. This one answers the imprecise question — "what is this",
"why doesn't my domain work", "what do I do now" — and answers it against the
live deployment rather than in general, because a walkthrough that does not
know whether you are signed in is a manual, not a guide.

Three things live here, and all three are grounded in the same state:

  checklist()  what to do next, in order, each step already marked done or not
               by looking at the box: is the listener up, are you signed in, do
               you own a zone, is that zone actually delegated to us.

  ask()        a question in plain words in, a plain answer out. It reads any
               name in the question and really resolves it, so "why is eth
               broken" comes back with what eth actually does right now rather
               than with advice.

  term()       the vocabulary, in one-line and paragraph form, each entry
               showing what the word means *here* — the apex of this zone, the
               wildcard this deployment derives, the TTL it actually serves.

There is no model in this file and no network call to one. Every sentence it
returns was written down in advance and selected by matching, so it is the same
answer offline, on a box with no keys, and inside an agent that called
`dns_ask` over MCP. What it will not do is guess: a question it cannot place
comes back saying so, with the closest things it does know.
"""
import re

import fleet
import identity
import resolver
import server
import settings
import zone as Z

# ── the live picture the answers are written against ─────────────────────


def context(token=None):
    """Everything an answer might need to be about *this* box."""
    s = settings.all()
    host = settings.host()
    address = identity.whoami(token)
    owner = identity.owner()
    zones = Z.zones()
    mine = [z['zone'] for z in zones
            if address and Z.can_write(z['zone'], address, owner)]
    sys_zone = next((z for z in zones if z['zone'] == Z.system_zone()), None)
    target = (Z.target_of(sys_zone)[0] if sys_zone else None) or s.get('target')
    mods = fleet.modules()
    # server.state() only knows about a listener in *this* process. The CLI and
    # the stdio MCP transport are other processes, so fall back to knocking on
    # the port: a guide that told you the name server was down because it was
    # asked from a shell would be worse than useless.
    listener = server.state()
    if not listener['running'] and fleet.port_live(listener['port']):
        listener = dict(listener, running=True, transports=['udp', 'tcp'],
                        elsewhere=True)
    return {
        'host': host,
        'target': target,
        'ttl': s.get('ttl'),
        'owner': owner,
        'address': address,
        'role': identity.role(address),
        'signed_in': bool(address),
        'is_owner': identity.is_owner(address),
        'zones': zones,
        'zones_yours': mine,
        'system_zone': Z.system_zone(),
        'unclaimed': not owner,
        'modules': mods,
        'module_names': [m['name'] for m in mods],
        'modules_live': sum(1 for m in mods if m.get('live')),
        'listener': listener,
        'dns_port': s.get('dns_port'),
        'wildcard': s.get('wildcard'),
        'module_names_on': s.get('module_names'),
    }


def _some(names, n=3):
    return ', '.join(names[:n]) if names else 'eth'


def _first_module(c):
    for want in ('eth', 'agent', 'store', 'chain'):
        if want in c['module_names']:
            return want
    return c['module_names'][0] if c['module_names'] else 'eth'


# ── the vocabulary ───────────────────────────────────────────────────────
# Each entry: a one-line meaning, a paragraph that assumes nothing, and a
# `here` line that is filled in from the live deployment so the word lands on
# something the reader can go and look at.

def _terms(c):
    host, target, mod = c['host'], c['target'] or 'this box', _first_module(c)
    return {
        'dns': (
            'the phone book of the internet: names in, addresses out',
            'Computers talk to numbers, people remember words. DNS is the '
            'system that turns one into the other. When you type a name into '
            'a browser, something has to say which machine that name means; '
            'DNS is the chain of servers that answers, and this module is one '
            'of those servers for one set of names.',
            f'this box answers authoritatively for {host} and anything '
            f'registered here'),
        'zone': (
            'one domain and everything under it, as one file you own',
            'A zone is a domain plus all the names beneath it, managed as a '
            'single unit by a single owner. If you own the zone for '
            'example.com, you decide what example.com, www.example.com and '
            'anything-else.example.com mean. Owning a zone here is not the '
            'same as owning the domain at a registrar — you need both, and '
            'the second one points the first at us.',
            f'{len(c["zones"])} zone(s) served here; {c["system_zone"]} is the '
            f'system zone'),
        'record': (
            'one line in a zone: a name, a type and a value',
            'Everything a name server knows is stored as records. A record '
            'says: for this NAME, when asked for this TYPE, answer this '
            'VALUE, and let the answer be cached for this many seconds. That '
            'is the whole data model. The types are the interesting part — an '
            'A record holds an address, a TXT record holds text, and so on.',
            f'ask for one: dns/records or the ZONE tab'),
        'a': (
            'the record that maps a name to an IPv4 address',
            'The A record is the one that actually gets you somewhere. Its '
            'value is a plain IPv4 address like 45.11.56.54, and it says "the '
            'machine for this name is at that number". If a name does not '
            'work, the A record is nearly always what is wrong or missing.',
            f'{host} has an A record pointing at {target}'),
        'aaaa': (
            'the same as an A record, for IPv6 addresses',
            'IPv6 is the newer, much longer form of address. An AAAA record '
            'holds one. You do not need it unless the box has an IPv6 address '
            'and you want people on IPv6-only networks to reach it.',
            'set one with the target_v6 field when you point a zone'),
        'cname': (
            'an alias: "for this name, go ask about that other name instead"',
            'A CNAME does not hold an address. It holds another name, and the '
            'resolver starts over with that one. It is how you say "blog.mine '
            'is really the same thing as mine.example.com" without repeating '
            'the address in two places. The catch: a name with a CNAME cannot '
            'have other records, so you cannot put one on the apex.',
            'this server follows CNAME chains for you when it answers'),
        'txt': (
            'free text attached to a name, read by machines not people',
            'A TXT record holds a string. Nothing routes traffic because of '
            'it. It is used to prove things and to publish facts: mail policy, '
            'ownership challenges, and — here — who a module belongs to.',
            f'_mod.{mod}.{host} is a TXT record saying whose module that is'),
        'mx': (
            'where mail for this domain should be delivered',
            'An MX record names the mail server for a domain and a priority '
            'number, lower being preferred. It matters only if you receive '
            'email at the domain. Web traffic ignores it completely.',
            'unset here — this deployment serves names, not mail'),
        'ns': (
            'which name servers are in charge of a zone',
            'An NS record names a server that is authoritative for a zone. '
            'This is how the internet finds its way to us: your registrar '
            'publishes NS records for your domain that name this box, and '
            'from then on every resolver in the world asks us.',
            f'this zone names ns1.{host} and ns2.{host}'),
        'soa': (
            'the "start of authority" line: who runs the zone and how it ages',
            'Every zone has exactly one SOA record. It carries the primary '
            'name server, an admin email, a serial number that changes when '
            'the zone changes, and the timers other servers use to decide how '
            'long to keep things. You almost never write one by hand.',
            f'derived automatically for every zone served here'),
        'ttl': (
            'how many seconds an answer may be remembered before re-asking',
            'Time To Live. When a resolver gets an answer it keeps it for the '
            'TTL and serves it from memory to everyone who asks. That makes '
            'DNS fast and it is also why a change you just made does not '
            'appear straight away: somebody is still holding the old answer '
            'until it expires.',
            f'the default here is {c["ttl"]} seconds — {c["ttl"] // 60 or 1} '
            f'minute(s)'),
        'apex': (
            'the bare domain itself, with nothing in front of it',
            'The apex — also called the root of the zone, and written @ — is '
            'example.com as opposed to www.example.com. It is special because '
            'it must carry the SOA and NS records, which is why it cannot be '
            'a CNAME.',
            f'the apex of the system zone is {host}; write it as @'),
        'wildcard': (
            'one record that answers for every name nobody wrote down',
            'A wildcard, written *, matches any name in the zone that has no '
            'record of its own. It is why a brand new module is reachable the '
            'moment it exists: nobody had to add DNS for it, the wildcard '
            'already answers.',
            ('on — *.%s answers for anything undefined' % host)
            if c['wildcard'] else 'off in this deployment'),
        'delegation': (
            'your registrar handing your domain over to this server',
            'Buying a domain gets you the right to say who answers for it. '
            'Delegation is the act of saying it: at your registrar you replace '
            'the default name servers with ours, and from that moment the '
            'internet asks us instead of them. Nothing you do on this box can '
            'do this for you — it happens at the registrar.',
            f'point your domain at ns1.{host} and ns2.{host}'),
        'glue': (
            'the address of a name server, published by the parent',
            'A chicken and egg problem: if ns1.example.com is the server for '
            'example.com, you cannot look up ns1.example.com without already '
            'knowing example.com. Glue is the fix — the parent zone publishes '
            'the address alongside the NS record, breaking the loop. Your '
            'registrar calls it "register a host" or "glue record".',
            f'needed only if your name servers live inside your own domain'),
        'resolver': (
            'the server your computer asks, which asks everyone else',
            'You do not talk to authoritative servers like this one directly. '
            'Your computer asks a resolver — your ISP\'s, or 1.1.1.1, or '
            '8.8.8.8 — and the resolver does the walking and caches what it '
            'learns. The CHECK tool here asks a public resolver the same '
            'question we answer, so you can see the two versions side by side.',
            'CHECK on the YOUR HOST tab uses 1.1.1.1 by default'),
        'authoritative': (
            'the server that holds the truth for a name, not a copy of it',
            'An authoritative server does not look anything up; it simply '
            'knows, because it holds the zone. Everything else on the internet '
            'holds cached copies of what it said. This module is authoritative '
            'for the zones it serves.',
            f'listening on {c["dns_port"]}, '
            + ('running' if c['listener'].get('running') else 'currently down')),
        'nxdomain': (
            '"that name does not exist here" — the strongest possible no',
            'A resolver has three ways to disappoint you. NXDOMAIN means the '
            'name itself is unknown. NODATA means the name exists but has no '
            'record of the type you asked for — a name with only a TXT record '
            'gives NODATA for A. And SERVFAIL means something broke. Telling '
            'them apart tells you what to fix: NXDOMAIN, write the record; '
            'NODATA, you asked for the wrong type.',
            'the RESOLVE tab prints the rcode with every answer'),
        'nodata': (
            'the name exists, but not with the type you asked for',
            'You asked for an A record; the name has only TXT. That is NODATA '
            '— not an error, just a different question than the one the zone '
            'can answer. It is a very common cause of "it says nothing is '
            'there" when something plainly is.',
            'switch the type dropdown on RESOLVE and ask again'),
        'propagation': (
            'the wait while old cached answers expire everywhere',
            'There is no push. When you change a record, resolvers that '
            'already asked keep their old copy until its TTL runs out, and '
            'they all started their clocks at different moments. So a change '
            'is instant here and gradual out there. Lower the TTL *before* a '
            'change you know is coming, not after.',
            f'with a TTL of {c["ttl"]}s, expect everyone current within about '
            f'{max(1, (c["ttl"] or 300) // 60)} minutes'),
        'token': (
            'a signature that proves which address is asking',
            'There are no passwords here. To make a change you sign a short '
            'message with your wallet; the signature is the token, and the box '
            'recovers your address from it. It is not a transaction, it costs '
            'nothing, and it moves nothing. Reading needs no token at all.',
            ('signed in as ' + c['address']) if c['signed_in']
            else 'not signed in — reads still work'),
        'owner': (
            'the address that holds this deployment and its system zone',
            'One address owns the box: the protocol host, the system zone, '
            'the listener and the settings. Everyone else is a holder, and a '
            'holder is not a second-class citizen — a holder registers their '
            'own zone and owns every record in it outright, without asking the '
            'owner for anything.',
            (f'owner is {c["owner"]}' if c['owner']
             else 'unclaimed — the first signed owner-operation claims it')),
        'holder': (
            'any signed caller: owns the zones they registered, nothing else',
            'Standing here has three levels. Anonymous can read everything. A '
            'holder — anyone who signed in — can register a domain of their '
            'own and fully control it. The owner additionally controls the '
            'system zone and the box itself.',
            f'you are: {c["role"]}'),
        'attribution': (
            'the record that publishes whose module a name is',
            'Beside the address for every module, this zone publishes a TXT '
            'record saying who the module declares as its owner, the CID of '
            'its schema, and the key this box signs its module card with. It '
            'means you can ask DNS, from anywhere, whose code is behind a name '
            '— and then check the claim against a signature.',
            f'dig +short TXT _mod.{mod}.{host}'),
        'cid': (
            'a content address: the fingerprint of the module\'s schema',
            'A CID identifies data by what it is rather than where it lives. '
            'Two copies of the same module schema have the same CID anywhere '
            'in the world, so publishing it in DNS lets anyone confirm they '
            'are looking at the same module you meant.',
            'shown per module on the ATTRIBUTION tab'),
        'module': (
            'one small app in the fleet, with its own name and address',
            'The mod protocol builds systems out of modules: a directory with '
            'a config.json, a port, an API and usually a console. A module '
            'that declares "route": true gets a name in this zone '
            'automatically — nobody adds DNS for it by hand.',
            f'{len(c["modules"])} routed here, {c["modules_live"]} of them up '
            f'right now'),
        'gateway': (
            'the HTTP router that makes {host}/{mod} reach the right port',
            'DNS gets you to the box. The gateway gets you to the module: it '
            'reads the path and forwards to the port that module listens on. '
            'The two halves have to agree about the host name, which is why '
            'this module can hand its host to the router.',
            f'{host}/{mod} is the app, {host}/api/{mod} is the API'),
        'listener': (
            'the name server process itself, bound to a port',
            'Answering DNS means holding a UDP and TCP port open and replying '
            'to raw query packets. That is the listener. Real DNS lives on '
            'port 53, which needs root, so this runs on a high port and is '
            'reached either directly or by forwarding 53 to it.',
            (f'up on {c["listener"].get("bind")}:{c["listener"].get("port")}'
             if c['listener'].get('running') else 'down')),
        'derived': (
            'a record computed from the fleet, not typed by anyone',
            'Most of what this zone contains was never written down. The '
            'module names, the wildcard, the attribution records, the SOA and '
            'NS — all of them are computed from the module fleet every time '
            'they are asked for. That is why adding a module needs no DNS '
            'change, and why deleting a derived record does nothing: write '
            'over it instead, and the stored record wins.',
            'the ZONE tab labels every record derived or stored'),
        'stored': (
            'a record somebody actually wrote, which beats a derived one',
            'Anything you write is stored, attributed to your address, and '
            'shadows the derived record of the same name and type. The ZONE '
            'tab shows you what got shadowed and why it existed.',
            'writes need a signature and zone ownership'),
        'challenge': (
            'the TXT record that proves the domain is really yours',
            'Registering a domain here is a claim. Publishing the challenge '
            'TXT record we hand you — at your registrar, where only the real '
            'owner can — is the proof. Until then the zone shows as claimed '
            'but unverified.',
            f'{Z.CHALLENGE}.<your domain>'),
        'mcp': (
            'the way an AI agent calls this module directly',
            'Model Context Protocol is a standard for exposing tools to an '
            'agent. Everything in this console is also an MCP tool on the same '
            'port, so Claude or any other agent can resolve names, read zones '
            'and — with your token — make changes, through the same functions '
            'and the same permission checks.',
            f'POST https://{host}/api/dns/mcp'),
    }


def glossary(token=None, word=None):
    c = context(token)
    t = _terms(c)
    if word:
        return term(word, token, _pre=(c, t))
    return {
        'terms': [{'word': k, 'means': v[0], 'here': v[2]}
                  for k, v in sorted(t.items())],
        'count': len(t),
        'note': 'ask for one by name to get the paragraph: '
                'GET /guide/term?word=zone',
    }


def term(word, token=None, _pre=None):
    c, t = _pre or (None, None)
    if t is None:
        c = context(token)
        t = _terms(c)
    key = re.sub(r'[^a-z0-9]', '', (word or '').lower())
    aliases = {'arecord': 'a', 'aaaarecord': 'aaaa', 'txtrecord': 'txt',
               'nsrecord': 'ns', 'mxrecord': 'mx', 'cnamerecord': 'cname',
               'records': 'record', 'zones': 'zone', 'modules': 'module',
               'nameserver': 'ns', 'nameservers': 'ns', 'delegate': 'delegation',
               'delegated': 'delegation', 'root': 'apex', 'star': 'wildcard',
               'cache': 'propagation', 'caching': 'propagation',
               'timetolive': 'ttl', 'signin': 'token', 'wallet': 'token',
               'auth': 'token', 'signature': 'token', 'servfail': 'nxdomain',
               'rcode': 'nxdomain', 'owners': 'owner', 'permissions': 'holder',
               'standing': 'holder', 'role': 'holder', 'glueRecord': 'glue',
               'contentid': 'cid', 'schema': 'cid', 'router': 'gateway',
               'caddy': 'gateway', 'proxy': 'gateway', 'server': 'listener',
               'port': 'listener', 'agent': 'mcp', 'claude': 'mcp'}
    key = aliases.get(key, key)
    if key not in t:
        near = [k for k in t if key and (key in k or k in key)]
        return {'word': word, 'known': False,
                'closest': near or sorted(t)[:8],
                'say': f'"{word}" is not a word this guide defines. '
                       f'GET /guide/glossary lists every one it does.'}
    means, long, here = t[key]
    return {'word': key, 'known': True, 'means': means, 'plainly': long,
            'here': here,
            'see_also': _related_terms(key)}


_TERM_LINKS = {
    'a': ['record', 'ttl', 'apex'], 'aaaa': ['a', 'record'],
    'cname': ['a', 'apex', 'record'], 'txt': ['attribution', 'challenge'],
    'ns': ['delegation', 'glue', 'zone'], 'soa': ['zone', 'apex'],
    'ttl': ['propagation', 'record'], 'apex': ['zone', 'wildcard', 'cname'],
    'wildcard': ['derived', 'module', 'apex'],
    'delegation': ['ns', 'glue', 'challenge'], 'glue': ['ns', 'delegation'],
    'resolver': ['authoritative', 'propagation', 'nxdomain'],
    'authoritative': ['resolver', 'listener', 'zone'],
    'nxdomain': ['nodata', 'resolver'], 'nodata': ['nxdomain', 'record'],
    'propagation': ['ttl', 'resolver'], 'token': ['owner', 'holder'],
    'owner': ['holder', 'zone'], 'holder': ['owner', 'token', 'zone'],
    'attribution': ['txt', 'cid', 'module'], 'cid': ['attribution', 'module'],
    'module': ['gateway', 'wildcard', 'attribution'],
    'gateway': ['module', 'dns'], 'listener': ['authoritative', 'dns'],
    'derived': ['stored', 'wildcard', 'module'], 'stored': ['derived', 'record'],
    'challenge': ['delegation', 'txt', 'zone'], 'zone': ['record', 'apex', 'owner'],
    'record': ['a', 'ttl', 'zone'], 'dns': ['zone', 'resolver', 'record'],
    'mcp': ['module', 'token'], 'mx': ['record'],
}


def _related_terms(key):
    return _TERM_LINKS.get(key, ['dns', 'zone', 'record'])


# ── the checklist: what to do next, on this box, right now ───────────────

def checklist(token=None):
    """Ordered steps, each already marked done or not by looking at the box."""
    c = context(token)
    host, mod = c['host'], _first_module(c)
    yours = c['zones_yours']
    unverified = [z for z in c['zones']
                  if z['zone'] in yours and not z.get('verified')]
    steps = []

    steps.append({
        'id': 'look',
        'title': 'see what a name already means',
        'done': True,
        'why': 'Nothing here is set up before you can use it. Every module in '
               'the fleet already has a name, an address and four URLs, and '
               'looking one up costs nothing and needs no account.',
        'how': f'Type a module name — {_some(c["module_names"])} — into '
               f'RESOLVE. You get the app URL, the API URL, the MCP endpoint '
               f'and the actual DNS answer, all four computed from one thing.',
        'action': {'label': f'resolve {mod}', 'tab': 'resolve',
                   'fill': {'q': mod}, 'run': 'resolve'},
        'cli': f'm dns/resolve {mod}',
    })

    steps.append({
        'id': 'listener',
        'title': 'the name server is answering',
        'done': bool(c['listener'].get('running')),
        'why': 'Records are only worth something if a server is holding a '
               'port open and replying to real queries. This is that server.',
        'how': (f'It is up on {c["listener"].get("bind")}:'
                f'{c["listener"].get("port")}, over UDP and TCP.'
                if c['listener'].get('running') else
                'It is down. Only the deployment owner can start it — '
                'POST /serve, or `m dns/serve`.'),
        'action': {'label': 'see recent resolutions', 'tab': 'ops'},
        'cli': 'm dns/stats',
    })

    steps.append({
        'id': 'signin',
        'title': 'sign in, if you want to change anything',
        'done': c['signed_in'],
        'why': 'Reading is open to everyone. Changing a name has to be '
               'attributable, so a change is signed by the address making it. '
               'It is one wallet signature: no transaction, no gas, nothing '
               'moves, and it can be revoked by signing out.',
        'how': ('You are signed in as ' + (c['address'] or '')
                + f' with the standing "{c["role"]}".' if c['signed_in'] else
                'Press CONNECT WALLET in the top right and approve the '
                'signature. No wallet? Paste a token minted elsewhere with '
                'm.mod("auth")().token({}).'),
        'action': {'label': 'connect wallet', 'signin': True},
        'cli': 'm dns/whoami',
    })

    steps.append({
        'id': 'domain',
        'title': 'have a domain you control',
        'done': bool(yours) or c['is_owner'],
        'why': 'This is the one step no server can do for you. A domain is '
               'rented from a registrar, and only the person holding that '
               'account can point it anywhere.',
        'how': 'Buy one anywhere — Namecheap, Cloudflare, Porkbun, whoever. '
               'Any TLD works. If you only want to try the protocol out, you '
               'do not need one at all: every module already answers under '
               + host + '.',
        'action': {'label': 'plan a host', 'tab': 'host'},
    })

    steps.append({
        'id': 'register',
        'title': 'register your domain here',
        'done': bool(yours),
        'why': 'Registering creates the zone and makes you its owner — every '
               'record in it, its target, its deletion. It needs no permission '
               'from the deployment owner, and it does not touch the system '
               'zone or anybody else\'s names.',
        'how': ('You own: ' + ', '.join(yours) if yours else
                'YOUR HOST → register a host you control. Give the domain and '
                'the IP it should point at (this box, by default).'),
        'action': {'label': 'register a host', 'tab': 'host'},
        'cli': 'm dns/register yourdomain.com',
    })

    steps.append({
        'id': 'delegate',
        'title': 'point the domain at this box',
        'done': bool(yours) and not unverified,
        'why': 'Registering here is a claim. The internet only asks us once '
               'your registrar says to — that is delegation, and it happens in '
               'the registrar\'s control panel, not here.',
        'how': f'At your registrar, either set A records for yourdomain.com '
               f'and *.yourdomain.com to {c["target"]}, or set the name '
               f'servers to ns1.{host} and ns2.{host}. Then publish the '
               f'challenge TXT record we give you and press verify.',
        'action': {'label': 'check what the internet sees', 'tab': 'host',
                   'run': 'check'},
        'cli': 'm dns/check yourdomain.com',
    })

    steps.append({
        'id': 'route',
        'title': 'let the HTTP side follow',
        'done': bool(yours) and not unverified,
        'why': 'DNS gets a browser to the box. The router decides which module '
               'a path reaches. They have to agree on the host name or the '
               'domain resolves to a machine that will not serve it.',
        'how': 'On the box that runs the modules, `m caddy/add_host '
               'yourdomain.com`. This is the only step that needs the box '
               'owner, because it edits the live router.',
        'cli': 'm caddy/add_host yourdomain.com',
    })

    done = sum(1 for s in steps if s['done'])
    nxt = next((s for s in steps if not s['done']), None)
    return {
        'host': host,
        'you': {'address': c['address'], 'role': c['role'],
                'signed_in': c['signed_in'], 'zones': yours},
        'steps': steps,
        'done': done,
        'total': len(steps),
        'next': nxt['id'] if nxt else None,
        'say': (f'{done} of {len(steps)} done.'
                + (f' Next: {nxt["title"]}.' if nxt else
                   ' Nothing left — your host is live.')),
        'shortcut': f'You do not have to do any of it to use the protocol: '
                    f'every module already answers at {host}/<module>. The '
                    f'list above is for putting it on a domain of your own.',
        'ask': suggestions(c),
    }


def suggestions(c=None, token=None):
    """Questions worth asking, chosen for where this caller actually is."""
    c = c or context(token)
    mod = _first_module(c)
    out = ['what is this thing?', f'where do I find {mod}?']
    if not c['signed_in']:
        out.append('do I need a wallet?')
    if not c['zones_yours']:
        out += ['I have a domain — how do I use it here?',
                'I do not have a domain, do I need one?']
    else:
        out += ['how do I add a record?', 'why is my domain still not working?']
    out += ['what is a zone?', 'how long until my change works?',
            'can I break something?']
    return out[:8]


# ── asking in plain words ────────────────────────────────────────────────

_NAME_RE = re.compile(r'\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b', re.I)
_ASKING = re.compile(r'\b(why|what|how|when|who|which|where|whose|cant|cannot|'
                     r'couldnt|should|explain|help|tell|guide)\b')
# already stemmed the way _stem() below would leave them
_STATUS = {'up', 'down', 'live', 'run', 'runn', 'online', 'offline', 'work',
           'ok', 'alive', 'reachable', 'there', 'still', 'now', 'server',
           'this', 'thi', 'even'}
_IP_RE = re.compile(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b')
_STOP = set('''a an and are as at be but by can could do does for from get
have how i if in into is it its me my not of on or our so than that the their
this to want was what when where which who why will with would you your'''.split())


def _subject(q, c):
    """Any name the question is really about: a hostname, an IP, a module."""
    ip = _IP_RE.search(q)
    if ip:
        return {'kind': 'address', 'value': ip.group(1)}
    m = _NAME_RE.search(q)
    if m:
        v = m.group(1).lower().rstrip('.')
        return {'kind': 'host', 'value': v}
    for w in re.findall(r'[a-z0-9_-]{2,}', q.lower()):
        if w in _STOP:
            continue
        if w in c['module_names']:
            return {'kind': 'module', 'value': w}
    return None


def _grounding(subject, c):
    """Really look the subject up, so the answer is about it and not about DNS."""
    if not subject or subject['kind'] == 'address':
        return None
    try:
        d = resolver.resolve(subject['value'])
    except Exception:                                        # noqa: BLE001
        return None
    up = d.get('upstream') or {}
    facts = [
        {'k': 'name', 'v': d.get('name')},
        {'k': 'resolves to', 'v': ', '.join(d.get('addresses') or []) or 'nothing'},
        {'k': 'answer', 'v': (d.get('dns') or {}).get('rcode', '')},
    ]
    if d.get('module'):
        facts.append({'k': 'module', 'v': d['module'] +
                      (' — routed' if d.get('routed') else ' — not routed')})
        facts.append({'k': 'ports', 'v': f"api {up.get('api_port') or '—'} "
                                         f"({'up' if up.get('api_live') else 'down'}), "
                                         f"app {up.get('app_port') or '—'} "
                                         f"({'up' if up.get('app_live') else 'down'})"})
    urls = d.get('urls') or {}
    return {'resolve': d, 'facts': facts,
            'urls': [{'k': k, 'v': urls[k]} for k in
                     ('app', 'api', 'mcp', 'subdomain') if urls.get(k)]}


def _A(understood, answer, **kw):
    out = {'understood': understood,
           'answer': answer if isinstance(answer, list) else [answer]}
    out.update(kw)
    return out


def _intents(c):
    """(id, keywords, phrases, builder). Scored, best match wins."""
    host, mod = c['host'], _first_module(c)
    target = c['target'] or 'this box'

    def what_is_this(q, s, g):
        return _A(
            'what this module is',
            [f'This is the name layer of the mod protocol. Its job is to turn '
             f'names into addresses: you say "{mod}" and it says where {mod} '
             f'actually is — the page, the API, the MCP endpoint for agents, '
             f'and the raw IP behind the hostname.',
             f'It is also a real name server. It holds the zone for {host} and '
             f'answers DNS queries for it over UDP and TCP, the same way the '
             f'servers behind any domain do. The zone is not typed in by hand: '
             f'it is computed from the {len(c["modules"])} modules running on '
             f'this box, so a new module has a working name the moment it '
             f'exists.',
             'And it publishes who each module belongs to, in DNS, beside the '
             'address — so "whose code is this" is a question anyone can ask '
             'from anywhere without trusting this console.'],
            facts=[{'k': 'host', 'v': host},
                   {'k': 'points at', 'v': target},
                   {'k': 'modules named', 'v': f'{len(c["modules"])} '
                                               f'({c["modules_live"]} up)'},
                   {'k': 'you are', 'v': c['role']}],
            do=[{'label': f'try it — resolve {mod}', 'tab': 'resolve',
                 'fill': {'q': mod}, 'run': 'resolve'},
                {'label': 'show me where to start', 'tab': 'start'}],
            terms=['dns', 'zone', 'module'],
            related=['where do I start?', f'where do I find {mod}?',
                     'what is a zone?'])

    def start(q, s, g):
        cl = checklist_summary(c)
        return _A(
            'where to start',
            ['You do not have to set anything up to use this. Every module on '
             f'this box already has a name under {host}, already resolves, and '
             'is already reachable — try RESOLVE first, it costs nothing and '
             'needs no account.',
             'The setup steps only matter if you want the protocol answering '
             'on a domain of your own. There are four of them, and the START '
             'tab keeps score: sign in, register your domain, point it at this '
             'box at your registrar, then let the HTTP router follow.',
             cl],
            do=[{'label': 'open the checklist', 'tab': 'start'},
                {'label': f'resolve {mod}', 'tab': 'resolve',
                 'fill': {'q': mod}, 'run': 'resolve'}],
            terms=['zone', 'delegation', 'token'],
            related=['I have a domain — how do I use it here?',
                     'do I need a wallet?', 'can I break something?'])

    def find(q, s, g):
        if g:
            d = g['resolve']
            name = d.get('name')
            if d.get('module'):
                live = (d.get('upstream') or {}).get('api_live') or \
                    (d.get('upstream') or {}).get('app_live')
                lead = (f'{d["module"]} is a module on this box and it is '
                        f'{"running" if live else "NOT running right now"}. '
                        f'Here is every address it answers on.')
            else:
                lead = (f'{name} is a host, not a module. Here is what this '
                        f'server knows about it.')
            return _A(
                f'where {name} is',
                [lead,
                 'The four URLs are not four different systems. The path form '
                 'goes through the HTTP router, the subdomain form goes '
                 'through DNS, and both land on the same port on the same box '
                 '— which is exactly why they agree.'],
                facts=g['facts'], urls=g['urls'],
                do=[{'label': 'open the full answer', 'tab': 'resolve',
                     'fill': {'q': s['value']}, 'run': 'resolve'}],
                terms=['module', 'gateway', 'a'],
                cli=[f'm dns/resolve {s["value"]}'],
                related=[f'who owns {s["value"]}?',
                         f'why is {s["value"]} not working?'])
        return _A(
            'how to find a module',
            [f'Type its name into RESOLVE — {_some(c["module_names"], 4)} are '
             f'all valid — and you get four addresses back: the app page, the '
             f'API, the MCP endpoint an agent would call, and the hostname.',
             f'The rule never changes: {host}/<module> is the app, '
             f'{host}/api/<module> is the API, <module>.{host} is the same '
             f'thing over DNS. You can also paste a whole URL in and it will '
             f'work backwards from it.'],
            do=[{'label': 'browse every module', 'tab': 'fleet'},
                {'label': f'resolve {mod}', 'tab': 'resolve',
                 'fill': {'q': mod}, 'run': 'resolve'}],
            terms=['module', 'gateway'],
            related=['what is this thing?', 'how many modules are there?'])

    def own_domain(q, s, g):
        d = (s or {}).get('value') if (s or {}).get('kind') == 'host' else \
            'yourdomain.com'
        return _A(
            'using a domain you own',
            ['Four steps, and only two of them happen here.',
             'First, sign in — one wallet signature, no transaction. Second, '
             f'register {d} on the YOUR HOST tab; that creates the zone and '
             'makes you its owner, with no permission needed from anybody. '
             f'Third — and this is the part only you can do — go to your '
             f'registrar and either point {d} and *.{d} at {target} with A '
             f'records, or hand the whole domain over by setting its name '
             f'servers to ns1.{host} and ns2.{host}. Fourth, publish the '
             'challenge TXT record we hand you and press verify.',
             'Then the HTTP side: the box owner runs `m caddy/add_host ' + d +
             '` so the router serves your domain too. That is the one step '
             'you cannot do yourself.'],
            steps=[
                {'n': 1, 'do': 'sign in',
                 'how': 'CONNECT WALLET, top right — a signature, not a payment'},
                {'n': 2, 'do': f'register {d}',
                 'how': 'YOUR HOST → register. You own every record in it'},
                {'n': 3, 'do': 'point it here at your registrar',
                 'how': f'A records to {target}, or NS to ns1.{host} / ns2.{host}'},
                {'n': 4, 'do': 'prove it',
                 'how': f'publish the {Z.CHALLENGE} TXT record, then verify'},
                {'n': 5, 'do': 'route the HTTP side',
                 'how': f'the box owner runs m caddy/add_host {d}'}],
            do=[{'label': f'plan it for {d}', 'tab': 'host',
                 'fill': {'pname': d}, 'run': 'plan'},
                {'label': 'open the checklist', 'tab': 'start'}],
            terms=['zone', 'delegation', 'ns', 'challenge'],
            cli=[f'm dns/plan {d}', f'm dns/register {d}'],
            related=['why is my domain still not working?',
                     'what is a nameserver?', 'do I need a wallet?'])

    def no_domain(q, s, g):
        return _A(
            'whether you need a domain at all',
            ['No. Not to use any of this.',
             f'Every module on this box already has a working name under '
             f'{host} — {_some(c["module_names"], 3)} and '
             f'{max(0, len(c["modules"]) - 3)} more. You can resolve them, '
             f'open them, call their APIs and point an agent at them without '
             f'owning anything.',
             'A domain of your own only matters when you want the protocol to '
             'answer under your name instead of this one. If you decide you do '
             'want that: any registrar, any TLD, about ten dollars a year, '
             'then come back to YOUR HOST.'],
            do=[{'label': 'browse what already exists', 'tab': 'fleet'},
                {'label': f'resolve {mod}', 'tab': 'resolve',
                 'fill': {'q': mod}, 'run': 'resolve'}],
            terms=['zone', 'module'],
            related=['what is this thing?', 'where do I start?'])

    def broken(q, s, g):
        name = (s or {}).get('value')
        lines = ['DNS fails in four ways and they need four different fixes, '
                 'so the first move is always to find out which one you have.']
        facts = []
        if g:
            d = g['resolve']
            rcode = (d.get('dns') or {}).get('rcode')
            up = d.get('upstream') or {}
            facts = g['facts']
            if rcode == 'NXDOMAIN':
                lines.append(f'{d.get("name")} came back NXDOMAIN: this server '
                             f'has no record for that name at all. Either the '
                             f'name is misspelled, or it belongs to a zone that '
                             f'is not served here.')
            elif d.get('module') and not (up.get('api_live') or up.get('app_live')):
                lines.append(f'The name is fine — {d.get("name")} resolves to '
                             f'{", ".join(d.get("addresses") or []) or "nothing"}. '
                             f'What is down is the module itself: neither its '
                             f'API port ({up.get("api_port")}) nor its app port '
                             f'({up.get("app_port")}) is accepting connections. '
                             f'That is not a DNS problem, and no record change '
                             f'will fix it.')
            else:
                lines.append(f'{d.get("name")} resolves here to '
                             f'{", ".join(d.get("addresses") or []) or "nothing"} '
                             f'({rcode}). If it still does not work from '
                             f'outside, the next question is whether the public '
                             f'internet agrees with us — run CHECK.')
        lines.append('The four cases: NXDOMAIN means no such name — write the '
                     'record. NODATA means the name exists but not with the '
                     'type you asked for — ask for the right type. A record '
                     'that is right here but wrong outside means the domain is '
                     'not actually delegated to this box, or an old answer is '
                     'still cached. And a name that resolves perfectly to a '
                     'module that is down is not a DNS problem at all.')
        return _A(
            'why something is not working',
            lines, facts=facts,
            do=[{'label': 'compare against the public internet', 'tab': 'host',
                 'fill': ({'cname': name} if name else {}), 'run': 'check'},
                ({'label': f'resolve {name} again', 'tab': 'resolve',
                  'fill': {'q': name}, 'run': 'resolve'} if name else
                 {'label': 'see what the listener answered', 'tab': 'ops'})],
            terms=['nxdomain', 'nodata', 'propagation', 'delegation'],
            cli=[f'm dns/check {name or host}'],
            related=['how long until my change works?',
                     'what is a nameserver?'])

    def add_record(q, s, g):
        yours = c['zones_yours']
        if not c['signed_in']:
            body = ['You cannot yet, because writing a record has to be '
                    'attributable and nothing has signed for you. Sign in '
                    'first — one wallet signature, no transaction.',
                    'After that: register a domain you control, and you own '
                    'every record in it outright.']
        elif not yours:
            body = ['You are signed in, but you do not own a zone yet, and you '
                    f'can only write records into a zone you own. The system '
                    f'zone ({c["system_zone"]}) belongs to the deployment '
                    f'owner.',
                    'Register a domain of your own on YOUR HOST and every '
                    'record in it is yours.']
        else:
            body = [f'On the ZONE tab, pick {yours[0]} and use the write form '
                    'at the bottom. A record is four things: a name (@ for the '
                    'bare domain), a type (A for an address, TXT for text, '
                    'CNAME for an alias), a value, and a TTL in seconds.',
                    'Values are checked by encoding them to real wire format '
                    'before anything is saved, so a record that could not be '
                    'served is refused now rather than failing silently later.',
                    'Writing over a derived record shadows it — the stored one '
                    'wins, and the ZONE tab shows you what got shadowed.']
        return _A(
            'how to add a record',
            body,
            steps=[{'n': 1, 'do': 'name', 'how': '@ for the domain itself, or '
                    'a label like www — never the whole domain'},
                   {'n': 2, 'do': 'type', 'how': 'A for an IPv4 address, TXT '
                    'for text, CNAME for an alias, MX for mail'},
                   {'n': 3, 'do': 'value', 'how': 'the address, the text, the '
                    'target name — checked before it is saved'},
                   {'n': 4, 'do': 'ttl', 'how': f'seconds an answer may be '
                    f'cached; {c["ttl"]} is the default here'}],
            do=([{'label': f'open {yours[0]}', 'tab': 'zone'}] if yours else
                [{'label': 'register a host first', 'tab': 'host'}]),
            terms=['record', 'a', 'ttl', 'apex'],
            cli=['m dns/add name=www type=A value=' + str(target)],
            related=['what is an A record?', 'how long until my change works?'])

    def signin(q, s, g):
        return _A(
            'signing in',
            ['There is no password and no account. You prove who you are by '
             'signing a short message with a wallet, and the box recovers your '
             'address from the signature. That signed message is the token.',
             'It is not a transaction. Nothing is sent, nothing is spent, no '
             'gas, no approval of any contract. If your wallet shows a gas fee '
             'you are looking at the wrong prompt — this is a plain '
             'personal_sign.',
             'You never need it to read. Every lookup, every zone, every '
             'record and the whole change log are open to anyone. Signing in '
             'only unlocks making changes, and only to things you own.',
             'No wallet extension? Paste a token minted on a box that holds '
             'your key: m.mod("auth")().token({}).'],
            facts=[{'k': 'you', 'v': c['address'] or 'not signed in'},
                   {'k': 'standing', 'v': c['role']},
                   {'k': 'zones you can change',
                    'v': ', '.join(c['zones_yours']) or 'none yet'}],
            do=[{'label': 'connect wallet', 'signin': True},
                {'label': 'see what each standing may do', 'tab': 'ops'}],
            terms=['token', 'holder', 'owner'],
            cli=['m dns/whoami'],
            related=['can I break something?', 'who owns this deployment?'])

    def who_owns(q, s, g):
        name = (s or {}).get('value')
        return _A(
            'who a name belongs to',
            [f'Beside the address for every module, this zone publishes a TXT '
             f'record at _mod.<module>.{host} saying three things: the owner '
             f'address the module declares in its own config, the CID of its '
             f'schema, and the key this box signs its module cards with.',
             'That means "whose module is this" is answerable over plain DNS '
             'from anywhere, by anyone, without trusting this console — and '
             'the claim can then be checked against a signature, which is what '
             'the verify button on ATTRIBUTION does.',
             'A module that declares no owner publishes none. The record says '
             'what is declared, not what is proven; the signature is the '
             'proof.'],
            do=[{'label': 'open attribution', 'tab': 'attrib'}]
               + ([{'label': f'resolve {name}', 'tab': 'resolve',
                    'fill': {'q': name}, 'run': 'resolve'}] if name else []),
            terms=['attribution', 'txt', 'cid'],
            cli=[f'dig +short TXT _mod.{name or mod}.{host}',
                 f'm dns/attribution {name or mod}'],
            related=['what is a CID?', 'what is this thing?'])

    def how_long(q, s, g):
        ttl = c['ttl'] or 300
        return _A(
            'how long a change takes',
            ['Instantly here, gradually everywhere else, and the gap is the '
             'TTL.',
             f'Every answer this server gives carries a number of seconds — '
             f'{ttl} by default — and any resolver that receives it is allowed '
             f'to keep serving that answer from memory until the clock runs '
             f'out. Nothing pushes an update. So after a change, some people '
             f'see the new value at once and others see the old one for up to '
             f'{max(1, ttl // 60)} more minute(s).',
             'The trick everyone learns late: lower the TTL *before* you make '
             'a change you know is coming, wait for the old TTL to pass, then '
             'change the record, then put the TTL back. Lowering it afterwards '
             'does nothing for answers already cached.',
             'Delegation changes — moving name servers — are slower still, '
             'often hours, because the TLD publishes its own longer TTL.'],
            facts=[{'k': 'default ttl here', 'v': f'{ttl}s'},
                   {'k': 'so, worst case',
                    'v': f'about {max(1, ttl // 60)} minute(s) for a record'}],
            do=[{'label': 'see what the internet returns now', 'tab': 'host',
                 'run': 'check'}],
            terms=['ttl', 'propagation', 'resolver'],
            related=['why is my domain still not working?',
                     'what is a nameserver?'])

    def permissions(q, s, g):
        return _A(
            'who is allowed to do what',
            ['Three standings, and the difference matters less than newcomers '
             'expect.',
             'Anonymous — no signature — can read absolutely everything: every '
             'zone, every record, the whole change log, the resolutions the '
             'listener answered. Nothing here is hidden behind sign-in.',
             'A holder is anyone who signed. A holder registers domains of '
             'their own and owns every record in them completely: the target, '
             'the names, the deletion. That path needs no permission from '
             'anybody.',
             f'The owner is the single address holding this deployment — the '
             f'system zone {c["system_zone"]}, the protocol host, the listener '
             f'and the settings. '
             + ('This deployment is unclaimed: the first signed caller to run '
                'an owner operation becomes the owner.'
                if c['unclaimed'] else f'That is {c["owner"]}.')],
            facts=[{'k': 'you are', 'v': c['role']},
                   {'k': 'owner', 'v': c['owner'] or 'unclaimed'},
                   {'k': 'your zones',
                    'v': ', '.join(c['zones_yours']) or 'none'}],
            do=[{'label': 'the full operation catalog', 'tab': 'ops'}],
            terms=['owner', 'holder', 'zone'],
            cli=['m dns/operations'],
            related=['can I break something?', 'do I need a wallet?'])

    def safety(q, s, g):
        return _A(
            'whether you can break something',
            ['Not by reading, and not by accident.',
             'Reads change nothing and need nothing. Every write is checked '
             'against what you own before it happens, so the worst you can do '
             'to somebody else\'s names is get refused — and the refusal comes '
             'back saying who may do the thing and what you can do instead.',
             'Inside your own zone you can certainly point a name at the wrong '
             'place, but every change is logged with your address and the '
             'before/after value, so it is visible and reversible. The system '
             'zone is not yours to damage, and derived records cannot be '
             'deleted at all — writing over one only shadows it.',
             'The genuinely risky action in DNS is at your registrar, not '
             'here: moving name servers away from something that works. Check '
             'first, move second.'],
            do=[{'label': 'see every change ever made here', 'tab': 'ops'}],
            terms=['stored', 'derived', 'owner'],
            related=['who is allowed to do what?', 'how do I add a record?'])

    def nameservers(q, s, g):
        return _A(
            'nameservers and delegation',
            ['A nameserver is a machine that answers DNS questions for a '
             'domain. Delegation is the act of saying which machine that is.',
             'When you buy a domain, the registrar points it at their own '
             'nameservers by default. To make this box authoritative you '
             f'replace them with ns1.{host} and ns2.{host} in the registrar\'s '
             f'control panel. From then on every resolver in the world that '
             f'wants your domain asks us.',
             'The lighter alternative, if you would rather keep your '
             f'registrar\'s DNS: leave the nameservers alone and just add A '
             f'records for your domain and *.yourdomain pointing at {target}. '
             f'That reaches the same box; it just means we are not the ones '
             f'answering.',
             'If your nameservers live inside the domain they serve, the '
             'registrar also needs glue — the address published by the parent, '
             'breaking the circular lookup. Registrars call it "register a '
             'host".'],
            do=[{'label': 'plan it for a domain', 'tab': 'host'},
                {'label': 'check delegation', 'tab': 'host', 'run': 'check'}],
            terms=['ns', 'delegation', 'glue', 'authoritative'],
            related=['I have a domain — how do I use it here?',
                     'how long until my change works?'])

    def record_types(q, s, g):
        return _A(
            'the record types',
            ['A record is a name, a type and a value. The type decides what '
             'the value means.',
             'A — an IPv4 address. This is the one that actually gets you to a '
             'machine. AAAA is the same thing for IPv6. CNAME — another name, '
             'meaning "go ask about that instead"; a name with a CNAME cannot '
             'have anything else, so it can never sit on the bare domain. TXT '
             '— free text, used for proofs and policies, which is where the '
             'attribution records live. MX — where mail goes. NS — which '
             'servers are in charge. SOA — the one administrative record every '
             'zone has.',
             'If a lookup says nothing is there, check you asked for the right '
             'type before concluding the name is missing. A name with only a '
             'TXT record answers NODATA to a question about A.'],
            do=[{'label': 'read the glossary', 'tab': 'start', 'glossary': True},
                {'label': 'look at a real zone', 'tab': 'zone'}],
            terms=['a', 'cname', 'txt', 'ns', 'record'],
            related=['how do I add a record?', 'what is a zone?'])

    def listener_q(q, s, g):
        l = c['listener']
        return _A(
            'the name server itself',
            [('The listener is up on '
              f'{l.get("bind")}:{l.get("port")}, answering on UDP and TCP.'
              if l.get('running') else
              'The listener is down right now — this box is not answering DNS '
              'queries. Only the deployment owner can start it.'),
             'Real DNS lives on port 53, and binding a port under 1024 needs '
             f'root, so this runs on {c["dns_port"]} instead. In production the '
             'box forwards 53 to it, or runs it with the privilege to bind 53 '
             'directly. Nothing about the answers changes either way.',
             'You can point a resolver straight at it to test: '
             f'dig @{c["target"] or "<box-ip>"} -p {c["dns_port"]} {mod}.{host}'],
            facts=[{'k': 'running', 'v': 'yes' if l.get('running') else 'no'},
                   {'k': 'port', 'v': str(c['dns_port'])},
                   {'k': 'transports', 'v': 'udp + tcp'}],
            do=[{'label': 'recent resolutions', 'tab': 'ops'}],
            terms=['listener', 'authoritative', 'resolver'],
            cli=[f'dig @{c["target"] or "<box-ip>"} -p {c["dns_port"]} '
                 f'{mod}.{host}'],
            related=['what is this thing?', 'why is my domain not working?'])

    def agents(q, s, g):
        return _A(
            'using this from an agent or the terminal',
            [f'Everything in this console is also an MCP tool, on the same '
             f'port. Point Claude or any other agent at '
             f'https://{host}/api/dns/mcp and it gets the same functions with '
             f'the same permission checks — including this guide, as dns_ask.',
             'From a shell, the module is a CLI: m dns/resolve eth, '
             'm dns/check yourdomain.com, m dns/plan yourdomain.com, '
             'm dns/ask "why is my domain broken".',
             'And it is a plain REST API with no client library needed: '
             f'curl https://{host}/api/dns/resolve?query={mod}'],
            do=[{'label': 'the whole tool list', 'tab': 'ops'}],
            terms=['mcp', 'module', 'token'],
            cli=[f'm dns/ask "what is a zone"',
                 f'curl https://{host}/api/dns/resolve?query={mod}'],
            related=['what is this thing?', 'do I need a wallet?'])

    def zone_q(q, s, g):
        return _A(
            'what a zone is',
            ['A zone is a domain plus everything under it, owned and managed '
             'as one thing. Own the zone for example.com and you decide what '
             'example.com, www.example.com and anything.example.com mean.',
             f'The zone here is unusual in one way: most of it was never typed '
             f'in. The module names, the wildcard, the attribution records, the '
             f'SOA and NS lines are all computed from the '
             f'{len(c["modules"])} modules on this box every time they are '
             f'asked for. That is why a new module has a working name '
             f'immediately and nobody edits DNS to ship.',
             'Records you write are stored, attributed to your address, and '
             'take precedence over the derived ones of the same name and type.'],
            facts=[{'k': 'zones served here', 'v': str(len(c['zones']))},
                   {'k': 'system zone', 'v': c['system_zone']},
                   {'k': 'yours', 'v': ', '.join(c['zones_yours']) or 'none'}],
            do=[{'label': 'open the zone', 'tab': 'zone'}],
            terms=['zone', 'derived', 'stored', 'apex'],
            related=['how do I add a record?',
                     'I have a domain — how do I use it here?'])

    # (id, tie-break weight, keywords, phrases somebody would really type)
    return [
        ('what_is_this', 8,
         'what is this what does this do what am i looking at explain this '
         'module purpose point overview introduce yourself who are you help',
         'what is this|what does this|what am i looking at|what is dns|'
         'explain this|who are you|what is the point|what is dns for',
         what_is_this),
        ('start', 8,
         'where do i start how do i begin getting started first steps new here '
         'noob beginner what do i do next guide me walk me through setup',
         'where do i start|how do i begin|getting started|get started|'
         'first step|new here|walk me through|what do i do|i am new|im new|'
         'help me|show me around|i am a noob|im a noob|complete beginner',
         start),
        ('find', 7,
         'where is find url address of reach open link how do i get to which '
         'port endpoint locate',
         'where is|where can i find|what is the url|how do i reach|'
         'how do i open|link to|address of|url for|how do i call',
         find),
        ('own_domain', 9,
         'my own domain i have a domain use my domain point my domain custom '
         'domain connect a domain hook up domain bought a domain namecheap '
         'cloudflare godaddy porkbun registrar transfer host my own',
         'my own domain|my domain|use my domain|point my domain|custom domain|'
         'connect my domain|i have a domain|i bought a domain|own host|'
         'my own host|set up my domain|hook up my domain|add my domain',
         own_domain),
        ('no_domain', 8,
         'do i need a domain no domain without a domain dont have a domain '
         'buy a domain where do i get a domain is a domain required',
         'do i need a domain|need a domain|without a domain|'
         'dont have a domain|do not have a domain|where do i get a domain|'
         'buy a domain|is a domain required',
         no_domain),
        # Weighted above own_domain on purpose: "my domain" appears in both
        # "how do I use my own domain" and "why is my domain not working", and
        # the second one is a diagnosis, not a walkthrough.
        ('broken', 10,
         'not working broken doesnt work cant reach unreachable down failing '
         'error wrong no answer refused timeout nothing happens why is it '
         'still fails dead resolving load',
         'not working|doesnt work|does not work|cant reach|can not reach|'
         'is broken|is down|no answer|nothing happens|why is it not|'
         'not resolving|not reachable|wont load|will not load|isnt working|'
         'is not working|stopped working|domain not working|'
         'still not working|nothing works|not come up',
         broken),
        ('add_record', 8,
         'add a record write a record create a record set a record point a '
         'name subdomain new record edit dns entry make a name update record '
         'delete a record',
         'add a record|write a record|create a record|set a record|new record|'
         'point a name|add a subdomain|delete a record|edit a record|'
         'change a record|make a subdomain',
         add_record),
        ('signin', 8,
         'sign in signin login log in wallet metamask token authenticate '
         'connect account do i need an account gas cost transaction is it safe '
         'to sign key signature',
         'do i need a wallet|sign in|log in|connect my wallet|'
         'is it safe to sign|does it cost|need an account|what is a token|'
         'do i need to pay|do i pay|any gas|cost me anything|why sign',
         signin),
        ('who_owns', 7,
         'who owns owner of attribution attributed belongs to whose module cid '
         'schema verify signed card provenance trust',
         'who owns|who made|whose module|belongs to|attributed to|'
         'who is behind|is it really|prove who',
         who_owns),
        ('how_long', 8,
         'how long propagate propagation wait cached cache stale still old ttl '
         'when will it update takes effect refresh',
         'how long|when will it|takes effect|still showing|still the old|'
         'not updated yet|propagate|why is it still|hasnt changed',
         how_long),
        ('permissions', 7,
         'allowed permission who can may i rights standing role admin '
         'privileges access control can i change',
         'who can|am i allowed|do i have permission|what can i do|'
         'who is allowed|am i able to',
         permissions),
        ('safety', 8,
         'break something dangerous risky safe mess up destroy ruin undo '
         'revert mistake careful worried scared',
         'break something|can i break|is it safe|mess up|screw up|will i ruin|'
         'can i undo|is this dangerous|what if i get it wrong',
         safety),
        ('nameservers', 8,
         'nameserver name server ns delegate delegation glue authoritative '
         'point the nameservers change nameservers',
         'name server|nameserver|change the nameserver|point the nameserver|'
         'delegate my domain|glue record|ns record|what is delegation',
         nameservers),
        ('record_types', 7,
         'record type a record aaaa cname txt mx srv caa what types kinds of '
         'records difference between',
         'what is an a record|what is a txt|what is a cname|record types|'
         'types of record|kinds of record|difference between a|what is an mx',
         record_types),
        ('listener', 7,
         'listener server running port udp tcp dig bind is it up '
         'answering queries',
         'is the server running|what port|port 53|is it up|answering queries|'
         'use dig|with dig|is the listener',
         listener_q),
        ('agents', 7,
         'mcp agent claude api curl cli terminal command line script '
         'programmatically automate tool',
         'from an agent|from claude|from the terminal|command line|'
         'use the api|with curl|over mcp|in a script|from code',
         agents),
        ('zone', 7,
         'zone what is a zone zones zone file derived stored records in a zone',
         'what is a zone|zone file|what is in the zone|how does the zone',
         zone_q),
    ]


def checklist_summary(c):
    cl_done = []
    if c['listener'].get('running'):
        cl_done.append('the name server is answering')
    if c['signed_in']:
        cl_done.append(f'you are signed in as {c["role"]}')
    if c['zones_yours']:
        cl_done.append('you own ' + ', '.join(c['zones_yours']))
    if not cl_done:
        return ('Right now you are anonymous and own nothing here, which is a '
                'perfectly good place to read from.')
    return 'Where you actually stand: ' + '; '.join(cl_done) + '.'


def _stem(w):
    for suffix in ('ing', 'ed', 'es', 's'):
        if len(w) > 4 and w.endswith(suffix):
            return w[:-len(suffix)]
    return w


def _words(q):
    return {_stem(w) for w in re.findall(r'[a-z]+', q.lower())} - _STOP


def _score(q, keywords, phrases):
    """How well a question matches an intent.

    Two signals, deliberately crude and deliberately transparent: shared words
    after a rough stemming, and whole phrases somebody would actually type.
    A phrase is worth three words, because "not working" says far more about
    what is being asked than "not" and "working" do apart.
    """
    kw = {_stem(w) for w in keywords.split()}
    score = len(_words(q) & kw)
    ql = ' ' + re.sub(r'[^a-z0-9 ]', ' ', q.lower()) + ' '
    ql = re.sub(r'\s+', ' ', ql)
    for phrase in phrases.split('|'):
        if phrase and phrase in ql:
            score += 3
    return score


def ask(question, token=None):
    """A question in plain words, answered against this deployment."""
    q = (question or '').strip()
    c = context(token)
    if not q:
        return {'question': q, 'understood': None, 'confidence': 'none',
                'answer': ['Ask anything — in whatever words you have. There '
                           'is no syntax.'],
                'related': suggestions(c)}

    subject = _subject(q, c)
    grounding = _grounding(subject, c) if subject else None

    intents = _intents(c)
    scored = []
    for iid, weight, keywords, phrases, build in intents:
        n = _score(q, keywords, phrases)
        if n:
            scored.append((n, weight, iid, build))
    scored.sort(key=lambda x: (-x[0], -x[1]))

    # Typing a bare name, or asking whether one is up, is a lookup rather than
    # a riddle — the resolve card already says whether it is running. But
    # "why is eth down" is a question *about* eth, and the answer to it is not
    # the answer to "eth", so anything with an interrogative in it goes on to
    # be matched properly.
    rest = _words(q) - {w for w in re.findall(r'[a-z]+', (subject or {}).get('value', ''))}
    if subject and grounding and not _ASKING.search(q.lower()) \
            and not (rest - _STATUS):
        build = next(b for i, w, k, p, b in intents if i == 'find')
        out = build(q, subject, grounding)
        out.update(question=q, confidence='high', matched='find')
        return _finish(out, c, subject, grounding)

    if not scored:
        return _unknown(q, c, subject, grounding)

    top, weight, iid, build = scored[0]
    out = build(q, subject, grounding)
    out.update(question=q, matched=iid,
               confidence='high' if top >= 3 else 'medium' if top == 2 else 'low')
    if len(scored) > 1 and scored[1][0] >= max(2, top - 1):
        out['also_could_mean'] = scored[1][2]
    return _finish(out, c, subject, grounding)


def _finish(out, c, subject, grounding):
    """Attach the live lookup and the glossary lines the answer leans on."""
    if grounding and 'facts' not in out:
        out['facts'] = grounding['facts']
    if grounding and grounding.get('urls') and 'urls' not in out:
        out['urls'] = grounding['urls']
    if subject:
        out['about'] = subject
    words = out.get('terms') or []
    t = _terms(c)
    out['terms'] = [{'word': w, 'means': t[w][0]} for w in words if w in t]
    out.setdefault('related', suggestions(c))
    out.setdefault('do', [])
    return out


def _unknown(q, c, subject, grounding):
    """Say so, and offer the nearest thing actually known."""
    t = _terms(c)
    words = set(re.findall(r'[a-z]+', q.lower())) - _STOP
    near = [k for k in t if k in words or any(w in k for w in words if len(w) > 3)]
    out = {
        'question': q, 'understood': None, 'confidence': 'none',
        'answer': ['I could not place that one, and I would rather say so than '
                   'invent an answer. Everything below is something I do know.',
                   'This guide covers what the module is, finding a module, '
                   'putting the protocol on a domain you own, why a name is '
                   'not working, writing records, signing in, attribution, '
                   'caching, permissions and the vocabulary.'],
        'terms': [{'word': w, 'means': t[w][0]} for w in near[:5]],
        'do': [{'label': 'open the checklist', 'tab': 'start'},
               {'label': 'read the glossary', 'tab': 'start', 'glossary': True}],
        'related': suggestions(c),
    }
    if grounding:
        out['answer'].insert(
            1, f'What I can tell you is that {grounding["resolve"].get("name")} '
               f'resolves — the facts below are live.')
        out['facts'] = grounding['facts']
        out['urls'] = grounding['urls']
    return out


# ── one object for the console to open on ────────────────────────────────

def guide(token=None):
    c = context(token)
    return {
        'what': 'the part of this module that assumes you have never done this',
        'checklist': checklist(token),
        'glossary': glossary(token)['terms'],
        'ask': suggestions(c),
        'how': {'ask': 'POST /guide/ask {"question": "..."} — plain words in, '
                       'a grounded answer out',
                'term': 'GET /guide/term?word=zone — one word, in full',
                'mcp': 'dns_ask, dns_guide, dns_explain',
                'cli': 'm dns/ask "why is my domain not working"'},
        'honest': 'There is no language model behind this. Every answer was '
                  'written down in advance and selected by matching your '
                  'words, then filled in with what this box is actually doing. '
                  'It will say when it does not know.',
    }
