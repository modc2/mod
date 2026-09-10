# dns

The name layer of the mod protocol. A zone derived from the module fleet,
served authoritatively over UDP and TCP, with the protocol's attribution
published beside every name.

```
m dns/ask "why is my domain not working"   # plain words in, a plain answer out
m dns/resolve eth                 # a mod name → app, API, MCP, A record
m dns/attribution eth             # whose module that is: owner, CID, signed card
m dns/check modc2.com             # what we hold vs what the internet returns
m dns/records                     # the system zone, derived + stored
m dns/plan yourdomain.com         # run the protocol on a host of your own
m dns/serve                       # API + console + MCP + the name server
```

API `:5380` (`/api/dns`) · console `/dns` · MCP `POST /mcp` (28 tools) ·
name server `udp+tcp :15353`

## The problem it is shaped around

The protocol's URL rule is one line: `{host}/{mod}` is a module's app,
`{host}/api/{mod}` is its API. That rule needs two separate things to agree —
the router must have a route for the module, and the host must resolve to the
box the router runs on. The `caddy` module owns the first half. This module
owns the second.

So the zone is not a file somebody maintains. It is a **function of the
fleet**: every module whose `config.json` says `"route": true` gets a name, a
new module is named a moment after it appears, and a module that goes away
takes its name with it.

```
@              A     45.11.56.54      the box this zone points at
*              A     45.11.56.54      so a new module needs no DNS change
eth            A     45.11.56.54      routed module, also at /eth
_mod.eth       TXT   v=mod1 mod=eth key=0x7d7c… cid=Qmau24… ver=1.0.0 …
_mod           TXT   v=mod1 host=modc2.com key=0x7d7c… mods=53 …
_mod-challenge TXT   mod-dns=…        proves this zone is delegated here
ns1, ns2       A     45.11.56.54      the nameservers, and glue for them
@              SOA/NS                 serial rises on every change
```

Anything you write yourself is a *stored* record, and a stored record for a
`(name, type)` pair replaces the derived one — that is how you pin the apex to
a CDN, or add MX, TXT and CAA the protocol would never guess. Nothing is lost:
whatever you shadowed is still listed under `overridden`.

## Attribution

A name that resolves says where a module is answering. It does not say whose
it is. The mod protocol already knows, in two registers, and this module
publishes both:

- **declared** — `config.json` carries `owner` (an address) and `schema` (the
  module's IPFS CID). It is what the module says about itself, and what the
  on-chain Registry maps a module name to.
- **attested** — `m.info(<mod>)` is the protocol's module card, signed by the
  key of the box serving it and checkable with `m.verify_info`. It is what the
  *host* says, under a signature.

Every routed module therefore gets a record beside its address:

```
$ dig +short TXT _mod.build.modc2.com
"v=mod1 mod=build owner=0xd779eb61… key=0x7d7c3234… cid=QmboeTWXPN… ver=2.8.1
  orbit=orbit app=/build api=/api/build"
```

`owner=` is the module's own declaration and is simply absent when it declares
none — nothing here mints an owner or lets a module inherit the host's.
`key=` is the box, not the author: it says *this deployment serves this CID*,
which is a claim a signature can back.

```
GET /attribution                      the whole fleet, and how much of it declares anything
GET /attribution?name=eth             one module's card and the record that carries it
GET /attribution?name=eth&verify=1    plus the signed module card, checked
```

The `_mod` prefix cannot collide with a module named `mod`: a leading
underscore is not a legal hostname label, which is why SPF, DKIM and ACME all
live under one.

## The guide

DNS punishes newcomers twice: once with vocabulary — zone, apex, glue, NODATA
— and once with delay, because a mistake stays invisible until some resolver
somewhere answers wrong. `guide.py` is the half of this module written for
somebody who has never done it.

```
m dns/guide                       # the checklist, scored against this box
m dns/ask "how do I use my own domain"
m dns/explain apex                # one word, in plain english, as it applies here
```

Three surfaces, one state:

- **A checklist**, ordered, each step already marked done or not by looking at
  the deployment: is the listener answering, are you signed in, do you own a
  zone, is that zone actually delegated here. It also says the thing a setup
  wizard never says — that you do not have to do any of it, because every
  module already answers under the protocol host.
- **A glossary** of 32 words, each with a one-line meaning, a paragraph that
  assumes nothing, and a line saying what that word points at *here*: the apex
  of this zone, the TTL it really serves, the wildcard it derives.
- **`ask()`**, which takes plain words. It pulls any name out of the question
  and resolves it for real before answering, so "why is eth broken" comes back
  with what eth is doing right now rather than with advice. Answers carry the
  buttons that act on them — the console switches tab, fills the field and
  presses the button.

There is no model behind it and no network call to one. Every sentence was
written down in advance and selected by matching words and phrases, then
filled in with live state, so the answer is the same offline, on a box with no
keys, and inside an agent calling `dns_ask` over MCP. A question it cannot
place comes back saying so, with the closest things it does know — it does not
invent.

The console opens on it: a **START HERE** tab carrying the checklist and the
glossary, and an **ASK** dock reachable from every other tab. Where there is
room the dock takes a column of its own and the page — header included — moves
over to make it; below that it comes up as a sheet with the tabs still visible
behind it, because a guide that covers the button it just told you to press is
not a guide.

## Resolving

`resolve` takes whatever you happen to be holding — a module name (`eth`), a
hostname (`eth.modc2.com`), a gateway path (`modc2.com/api/eth`) or a whole URL
— and answers with the module, the host, all four addresses, whether the
upstream ports are actually listening, the DNS records behind the name, and who
the module is attributed to.

`check` is the honesty function. This server holds records; the internet has
its own opinion, and when they differ nothing works while everything looks
fine. `check` asks a public resolver the same question and names the shape of
the disagreement: *not published*, *not held here*, *proxied*, or *mismatch*.

## Running it on your own host

You do not need the deployment owner's permission to put the mod protocol on a
domain you control.

```
m dns/plan yourdomain.com          # the exact steps, and who does each one
m dns/register yourdomain.com target=1.2.3.4 token=<your mod-protocol token>
```

Registering makes you the owner of that zone here — its records, its target,
its deletion. Registration is a claim, not a proof, so the zone starts
`verified: false` until the challenge TXT shows up in the public DNS. The one
step you cannot do yourself is step 4: `m caddy/add_host yourdomain.com` edits
the live router and belongs to whoever owns the box.

## Who may change what

Reads are open, all of them — with one privacy carve-out. The *names* are
public, but the IP addresses behind them are masked (`x.x.x.x` / `x:x:x::x`)
in every HTTP and MCP response unless the caller is the deployment owner: what
a name points at is the owner's business, not a page any anonymous browser can
read the box's address off. The console draws a masked address as a PRIVATE
pill rather than a broken value. Loopback, the unspecified bind and the
well-known public resolvers stay visible (they identify nothing), and the
owner can turn the whole mask off with `settings.private_ips = false`. Note
the honest limit: a zone actually *delegated* to this box is still answered
over UDP/TCP to anyone who queries the listener — DNS itself cannot check a
token — so the mask protects the HTTP surface, which is where an address
would otherwise be casually read.

What is gated beyond that is change, and there are exactly three standings:

| | |
|---|---|
| **owner** | the deployment owner. The system zone (the protocol host itself), the settings, the listener and the router sync. Claimed by the first signed caller. |
| **holder** | any signed caller. Registers and fully owns their own host and every record in it. |
| **anon** | reads everything, changes nothing. |

Auth is the fleet's one shared identity: a mod-protocol token from
`m.mod('auth').token(...)`, sent as `Authorization: Bearer <token>`, verified
by the auth module rather than reimplemented here. In the browser that token is
minted by the wallet: CONNECT WALLET asks MetaMask to `personal_sign` the
envelope `{data, time}`, which is byte-for-byte what the auth module
re-serializes and verifies, so a wallet, the `m` CLI and another module all
arrive at the same lowercase address by the same door. One signature, no
transaction, no gas, and nothing but the token is kept in the browser. `GET /operations` is the
catalog — every operation with the standing it requires, as data. `GET /ops` is
the append-only log of every change ever made and the address behind it.

For a box with no wallet in front of it, `DNS_OPEN=1` collapses every caller
into one local identity. `whoami` always says when it is on.

## Files

| | |
|---|---|
| `wire.py` | DNS on the wire — messages, every record type this server writes, and a stub resolver for asking the public internet |
| `fleet.py` | the module fleet as a name space: who is routed, on what ports, attributed to whom |
| `attrib.py` | attribution — the `_mod` records, and the signed module card behind them |
| `zone.py` | zones: the derived layer, the stored layer, and who may write which |
| `server.py` | the authoritative listener — UDP and TCP, wildcards, CNAME chasing, NODATA vs NXDOMAIN |
| `resolver.py` | a mod name in, every address out; the diff against the public internet; the plan for your own host |
| `identity.py` | who is calling — one token, three standings |
| `ops.py` | the operation catalog and the change log |
| `guide.py` | the guide — checklist, glossary and plain-word answers, all grounded in the live box; no model |
| `actions.py` | the operations themselves — REST, MCP and CLI all call these |
| `api.py` | REST + MCP + console on one port, stdlib only |
| `mcp.py` | 28 MCP tools over Streamable HTTP or stdio |
| `console.html` | the browser console, served same-origin at `/dns` — wallet sign-in, the resolver, the zone, the register-your-own-host flow, an MCP tab that handshakes this module's own endpoint and calls its tools from the page, and the guide: a START HERE tab and an ASK dock on every other one |
| `fonts/` | the console's two pixel faces (Press Start 2P, VT323), served from `/dns/fonts` so the name layer needs no other name to resolve before it can draw itself |

## State and settings

Everything mutable lives in `~/.mod/dns`, never in the committed config:
`settings.json`, `zones/`, `owner.json`, `ops.jsonl`, `challenge.seed`.

`host` is the setting that matters — `modc2.com` is a default, not a constant.
Changing it moves every derived name, every resolver answer, and (with
`router_sync`) the HTTP routes too. By default the module reads the caddy
module's host so DNS and routing cannot silently disagree.

The listener binds `15353`, not `53`: port 53 needs root and usually already
has a stub resolver on it. Delegating for real means either forwarding 53 to
it or running it where 53 is free.

## Legacy

`mod-dns/` (Rust + libp2p, Kademlia + GossipSub) and `app/` (Next.js) are the
first prototype of this module, kept for reference. Nothing in the module calls
them — the implementation is the Python above, and its zone comes from the
fleet rather than from a P2P record store.
