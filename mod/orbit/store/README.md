# store

Share a picture publicly, or as a code that works once and expires.

Two ways to hand somebody an image, and they are deliberately not the same
thing:

| | what it is | who can see it | how long |
|---|---|---|---|
| **publish** | a permanent public URL | anyone who ever gets the link | forever |
| **grant** | a one-time code, usually shown as a QR | the first person to fetch it | N seconds |

A grant is the interesting one. It carries no identity — whoever holds the code
is the audience, which is exactly what you want when the code is on a screen
and somebody is pointing a phone at it. Two independent bounds hold it down:

- **time** — it stops working N seconds after it was minted, whether or not
  anyone ever scanned it.
- **use** — the first successful fetch burns it. The second gets `410`. So
  does the first person, twice.

Because the bounds are independent, *never scanning a code is exactly as safe
as scanning it once*. It dies on the clock either way.

---

## What a scan lands on

The QR code points at a **page**, not at the picture.

```
/v/<code>   the page about a one-time code   — opening it claims NOTHING
/g/<code>   the bytes behind that code       — fetching them BURNS it
/p/<id>     the page about a published image
/i/<id>     the published bytes
```

Somebody who points a phone at a code on a screen should land somewhere that
says what they are holding: that it works once, how long it has left, and a
button to save the picture before it stops existing. Bytes cannot say any of
that — they are an image, and then they are gone.

It also closes the accident this module used to have to shrug at. Things that
are not the recipient follow URLs: chat clients making previews, browsers
prefetching, scanners that open a link twice. When the QR pointed at `/g/`,
every one of those spent the grant. The page is inert, the claim happens when a
person presses the button on it, and a preview bot now renders a card that says
*one-time picture* while the code stays good for the person it was for.

The bytes routes are unchanged. `curl` a `/g/` link and you get the picture and
burn the code, which is what a script wants; `python3 mod.py claim` does exactly
that. Nothing redirects — both routes are real, and they are different doors.

---

## Read this before you look for `m store/...`

**This module is not reachable through the module registry, and cannot be.**

Module names in the mod protocol are derived from the directory path, and
`core/tree` applies the orbits in an order that lets `core` overwrite `orbit`.
There is already a `core/store`, so:

```python
import mod as m, inspect
inspect.getfile(m.mod('store'))
# /root/mod/mod/core/store/src/mod.py   — never orbit/store
```

The `name` field in `config.json` is decorative as far as resolution goes. This
directory was built here because that is where it was asked for, and it works
the way `orbit/chain` does — as an HTTP product on its own ports, and through
`python3 mod.py <fn>` directly. If you want it callable as `m <name>/fn`, the
directory has to be renamed; nothing else has to change. The previous occupant
of this exact path hit the same wall and became [`orbit/shelf`](../shelf).

Two consequences worth knowing:

- **State lives in `~/.mod/store-share/`, not `~/.mod/store/`.** That second
  directory is `core/store`'s live database — `access.db`, `owner.json`,
  `blobs/` and the market. Writing into it would be a collision with
  consequences rather than just a dead end.
- **`core/store` is a different product.** It is a general content-addressed
  store with ACLs, pools, a market and its own one-time tickets. This module is
  only about images, and it is small on purpose. If you want the big one, it is
  already there and already serves `/store` on the gateway.

---

## Using it

```bash
python3 mod.py share photo.jpg for=5m       # store it, mint a code, draw the QR
```

That is the whole errand in one line, because "show this to the person next to
me" is one errand and splitting it into three commands is how people end up
publishing something instead. The rest:

```bash
python3 mod.py                              # what is on the shelf
python3 mod.py help                         # every verb, in one screen
python3 mod.py add photo.jpg                # store it, private
python3 mod.py add photo.jpg public=True    # store it, published
python3 mod.py images                       # yours
python3 mod.py publish sunset.png           # give it a public URL
python3 mod.py grant latest for=30s         # a code good for 30 seconds
python3 mod.py qr e54c for=2m               # the same, drawn in the terminal
python3 mod.py grants                       # codes still live
python3 mod.py peek <code>                  # would it work? asking is free
python3 mod.py claim <code> out=/tmp/it.png # redeem it — this BURNS it
python3 mod.py revoke <code>                # kill one early
python3 mod.py rm sunset.png
python3 mod.py serve                        # api :50670, console :50671
```

```bash
python3 mod.py docs                         # the manual, as data
python3 mod.py mcp                          # the tools an agent gets
```

### You never have to type a sha256

An image's id is the sha256 of its bytes, which is the right key for a database
and a terrible thing to ask a person for. Anywhere a picture is named — the
CLI, the MCP tools, the resource URIs — you may write any of:

| write | means |
|---|---|
| `e54c50db…` (64 hex) | the id itself |
| `e54c` | any unique prefix of it, four characters or more |
| `sunset.png` | the name you stored it under |
| `latest` | the one you added most recently (`last`, `newest`) |

Two matches is an error naming both, never a guess. The verbs on the other end
of this publish things permanently and delete things permanently, and a
resolver that quietly picks a winner turns a typo into whichever of those you
happened to be running.

Durations work the same way: `for=30s`, `for=5m`, `for=2h`, `for=1d`, or a bare
number of seconds. `ttl_seconds=` still works and still means seconds, so
nothing written against the old spelling changed meaning.

A **code** is the exception. A prefix of one works, but only for codes you
minted yourself and only from the CLI or an MCP tool. The public `/g/` and
`/grant?code=` routes take the whole code and nothing shorter: the code *is*
the credential, and a public endpoint that accepts prefixes is one that can be
walked eight characters at a time.

The console at `http://127.0.0.1:50671/store/` does the same things across four
screens — **Library** (drop, paste or pick a picture), **Codes** (what you have
out, counting down, revocable in one click), **Published**, and **Docs & MCP**,
which is rendered from `/docs` and `/mcp/schema` so a route that changes cannot
be right in the code and stale on the page. Each screen is a URL fragment —
`/store/#codes`, `/store/#docs` — so a reload keeps your place and a tab is
something you can send somebody rather than describe to them.

### HTTP

```
GET    /                    what this is
GET    /health              is the index telling the truth about the disk
GET    /me                  who you are as far as this module is concerned
GET    /public              everything anyone here has published
GET    /images              yours
GET    /image?id=           one record
POST   /upload?name=&public=    raw image bytes as the body — no multipart
POST   /publish  {id}
POST   /unpublish {id}
DELETE /image?id=
POST   /grant {id, ttl_seconds}     mint a one-time code
GET    /grants                      yours, live ones
GET    /grant?code=                 peek — does not spend it
DELETE /grant?code=                 revoke
POST   /sweep                       forget grants that stopped mattering

GET    /docs                the manual — a readable page for a browser, JSON for
                            anything that asks for JSON. ?section= for one part,
                            ?format=html|json to override the negotiation.
POST   /mcp                 MCP over JSON-RPC 2.0 — one message, or a batch
GET    /mcp/schema          tools, prompts, resource templates, client config
DELETE /mcp                 a client ending its session

GET    /p/<id>              the page for a published picture
GET    /i/<id>              published bytes. No credential, no expiry.
GET    /b/<id>              the bytes of a picture YOU own, published or not
GET    /v/<code>            the page for a one-time code. Claims nothing.
GET    /g/<code>            claim a grant. THIS BURNS IT.
GET    /g/<code>/qr         a picture of the link. Does not burn it.
```

`/b/<id>` is the only read path that asks who is calling. Without it the owner
of a private picture would be the one person on earth who could not look at it,
which is why the console used to show empty squares for everything unpublished.
It answers `404` — not `403` — for anything that is not yours, so it cannot
enumerate what other people are holding either.

Upload takes the raw file as the request body rather than a multipart form.
There is nothing for a form encoding to add: the API sniffs the format from the
bytes itself, and the filename is a query parameter.

---

## How the one-time part actually works

`claim` does not read the grant and then write it. It issues one conditional
`UPDATE` and checks the rowcount:

```sql
UPDATE grants SET claimed = ?, claimed_by = ?
 WHERE code = ? AND claimed IS NULL AND expires > ?
```

The database picks the winner. Two phones scanning the same QR code in the same
millisecond is not a hypothetical — it is what happens when a code is on a
screen in front of a room — and a check-then-write hands the image to both of
them. There is a `SELECT` in the failure branch, but it runs *after* the update
has already refused, purely to turn "no" into "expired" or "already used". It
is advisory and it can never be what lets a claim through. There is a test that
starts twenty threads on one code and asserts exactly one winner.

A spent code is kept rather than deleted, and answers `410`. A deleted row is
indistinguishable from one that never existed, so a burned link would answer
`404` and the person holding it could not tell *someone got there first* from
*you typed it wrong*. `sweep` removes them a week later.

---

## What it will not store, and why

Formats are decided by **sniffing magic bytes** — never by the filename, never
by the `Content-Type` the uploader claims. png, jpeg, gif, webp and bmp are
accepted. **SVG is refused outright.** Every other image format is inert data;
SVG is a document that can carry script, and this is served from an origin the
whole fleet shares, so an uploaded SVG is a stored-XSS primitive aimed at the
neighbouring modules. Every image response also goes out under
`Content-Security-Policy: default-src 'none'; sandbox` with
`X-Content-Type-Options: nosniff` — the second line for the day the first check
turns out to be wrong.

An unpublished id and an id that never existed both answer `404`, so the public
read path cannot be used to probe for private pictures.

## Two sharp edges, stated plainly

**The code is still a credential in a URL.** The page in front of the bytes
means an automated fetch no longer spends a grant — a preview bot, a prefetch
and a `HEAD` all leave it live — but anyone the link reaches can press the
button on that page, and pressing it is the whole ceremony. A link forwarded to
the wrong chat is still a picture handed to the wrong person. That is why the
default TTL is sixty seconds and the ceiling is a day: mint the code when the
person is in front of you, not in advance. Anything longer than a day is
publishing with extra steps, and `publish` is right there.

**Publishing is not undoable.** `unpublish` takes the URL away, but copies
already made stay made. The table at the top of this file is the honest version.

## MCP

The same store, for an agent instead of a person. Eighteen tools, two prompts
and a set of resources, all going through the same `src/` the console and the
CLI do, so there is one implementation of every rule and three front doors onto
it.

```bash
python3 src/mcp.py                        # stdio, one JSON-RPC message per line
curl -s -XPOST -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
     http://127.0.0.1:50670/mcp           # the running API answers the same
curl -s http://127.0.0.1:50670/mcp/schema # tool list + a client config to paste
```

There is no separate MCP port. The API already listens and already knows who is
calling: over HTTP it hands the recovered address to the tool layer, so an agent
sees exactly the pictures a browser with the same token would, and a remote
caller with no token never gets that far. Over stdio there is no request to
authenticate and tools run as the box's local owner, like the CLI.

Two tools are deliberately not one tool with a flag:

- **`store_peek`** asks whether a code still works. Asking spends nothing.
- **`store_claim`** redeems it. That is what spending it *is* — a model that
  calls it to "check the link" has used the link up.

A flag would have a default, and a wrong default there cannot be undone.

### Pictures come back as pictures

`store_view`, `store_qr` and `store_claim` return MCP **image content blocks** —
the picture itself, not a paragraph describing one. `store_claim` especially:
by the time it answers, the code is spent, and that response is the only copy
anyone is going to get. A claim that returned metadata alone would have
destroyed the thing it was asked to fetch.

Anything past `STORE_SHARE_MAX_INLINE` (1.5 MB by default) is refused with the
URL to read instead, rather than spending a context window on one photograph.

### One call for the usual errand

`store_share` takes a path, base64 bytes, or something already stored; stores it
if it is new; and mints a one-time code for it. The picture stays private — the
code is the only way in. It exists because "send this to X" arriving as three
separate tool calls is three chances to reach for `store_publish` by mistake.

### Prompts and resources

Two prompts, because there are two ways to share and picking the wrong one is
the mistake this module exists to make hard: `share_with_one_person` names
`store_share` and explicitly forbids `store_publish`; `publish_forever` makes
the model look at the picture first and say out loud that it is the right one.

Resources are the nouns: `store://docs` and `store://docs/<section>` for the
manual, and `store://image/<id>` for each picture you own — so a client that
lets a person attach context by hand can, and a model can look at an image
without spending a tool call on it.

### Protocol notes

- **Batching.** A JSON-RPC array is answered with an array of the replies that
  carry ids. A batch of nothing but notifications gets `202` and no body.
- **Streamable HTTP.** A client that accepts only `text/event-stream` gets one
  `event: message` and the stream ends — there is nothing here that streams,
  and holding the socket open would only look like one.
- **Sessions.** `Mcp-Session-Id` is echoed rather than enforced: every request
  already carries its own identity in `Authorization`, so there is no session
  state to key. `DELETE /mcp` answers `204` so a clean disconnect is not logged
  as a failure.
- **Protocol versions.** `2025-06-18`, `2025-03-26` and `2024-11-05`; the
  version a client asks for is the one it gets back if it is one of those.

## Storage

Content-addressed: an image's id is the sha256 of its bytes, so the same
picture uploaded twice is stored once. The *row* is per-owner, though, keyed
`(id, owner)` — two people who upload the same bytes each get their own record,
their own visibility flag and their own grants, and one deleting it does not
delete the other's. The blob underneath is refcounted and removed only when the
last row referring to it is.

Visibility is asked per-blob on the read path: if *any* owner has published
these bytes, `/i/<id>` serves them. That is not a leak — the bytes were already
public by the time the question is asked — but it is why the answer is phrased
as "are these bytes public" and not "is your row public".

## Identity

The fleet's shared protocol auth (`m.mod('auth')`, EIP-191 over secp256k1). The
address recovered from a bearer token owns whatever it uploads. No user table,
no password, and the same identity works in every module that speaks it.

Over **loopback only**, an unauthenticated caller is treated as the local owner
from `~/.mod/store-share/owner.json`, so the CLI and a browser on the box do
not have to sign anything to use their own library. That shortcut is a full
takeover if the port is ever reachable, so it is conditioned on the interface
the server actually bound rather than on a config flag somebody flips without
reading this. Off loopback, no token means `401`, and there is no way to
configure otherwise.

`config.json` sets `route: false` for the matching reason: the public read path
has no credential on it at all, so routing this to the gateway publishes every
image anyone on the box has published. Turn that on deliberately.

## Dependencies

None required — stdlib and `sqlite3`. Two optional:

- **`segno`** — a pure-Python QR encoder. Without it the links still work and
  only the pictures of them go missing; the API says `501` on the QR routes and
  `GET /` reports `"qr": false`.
- **`pillow`** — reads image dimensions for the listing. Without it `width` and
  `height` are `null` and nothing else changes.

## Tests

```bash
python3 -m pytest tests/ -q      # 84 passing
```

`tests/conftest.py` redirects `STORE_SHARE_HOME` to a temporary directory
during collection, before anything imports the library — a fixture would be too
late and the suite would run against real pictures.
