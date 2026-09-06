<div align="center">

# Logo

**Brand marks for the mod protocol — owned by the module, not by the console that draws them.**

```
   module A ─┐
   module B ─┼──▶  logo :50760  ──▶  ~/.mod/logo/marks/{group}/{name}.json
   module C ─┘         ▲
                       │  mod-protocol token
                       │  signature must recover the TARGET module's owner
                    wallet
```

</div>

## The idea

Every module shows a mark somewhere — the cube in a console's corner, the icon
on a catalog card. Until now each module kept its own, which meant the process
that *displayed* the mark was also the process that could *change* it. A
console running as root on the host, holding the only gate on its own branding,
is a gate that means very little.

This module separates the two. The mark lives here; the authority to change it
lives with the module's owner. A console can render the editor, take the
owner's signature and forward it, and still be unable to repaint itself.

**The rule, in one line:** a mark may only be changed by the address in the
target module's own `config.json` (or a co-owner in `~/.mod/{module}/owners.json`),
proved with a mod-protocol token.

---

## Quick start

```bash
cd mod/orbit/logo
pip install -r api/requirements.txt
./serve.sh                       # pm2: logo-api :50760, logo-app :50761
```

- API — <http://localhost:50760>
- Console — <http://localhost:50761/logo>
- Behind the gateway — `modc2.com/logo` and `modc2.com/api/logo`

```bash
m logo/status
m logo/owner build                       # who may change build's mark
m logo/glyph build 'X'                   # sign with this box's key
m logo/url   build https://ex.com/m.png
m logo/upload build ./mark.png
m logo/reset build                       # back to the protocol cube
m logo/marks                             # every module that has set one
```

`m logo/…` writes go through the **same gate** the HTTP API uses: a token is
minted from the box's signing key and verified against the target module's
declared owner. Being on the host is not a privilege here.

---

## Auth

One door in, one rule out.

**The door** is the mod-protocol token — `base64url({data, time, key, signature})`
where the signature is an EIP-191 `personal_sign` over exactly
`JSON.stringify({data, time})`. It is verified by `m.mod('auth')` itself rather
than reimplemented here, so a browser wallet, a CLI key and a peer module all
arrive the same way. Send it as `Authorization: Bearer <token>`, or as
`x-mod-token` when `Authorization` is already carrying a different session.

```js
// what the console does — one signature, no challenge endpoint, no gas
const payload = { data: { scope: 'logo' }, time: Math.floor(Date.now() / 1000) };
const signature = await ethereum.request({
  method: 'personal_sign', params: [JSON.stringify(payload), address] });
const token = b64url(JSON.stringify({ ...payload, key: address, signature }));
```

**The rule** is that the recovered address must own the module being painted.
Owner resolution, in order:

| # | Source | Notes |
|---|---|---|
| 1 | `{module}/config.json` → `owner` | the module's own manifest, the normal case |
| 2 | `~/.mod/{module}/owners.json` | co-owners, off-chain by design; a bare array or `{"addresses": []}` |
| 3 | this deployment's owner | **only** for a module that declares no owner at all; first signed caller claims |

A module that declares no owner, on an unclaimed deployment, cannot be painted
by anyone. That is deliberate — a mark nobody owns is not a mark everybody owns.

Reads need nothing. A logo is the thing everyone sees; gating it on a session
would only make the corner flicker for visitors.

`LOGO_OPEN=1` drops the gate entirely for local development. `GET /status` and
the console both say so in red when it is set, because an open-mode deployment
lets any caller repaint every module in the fleet.

---

## API

| Route | Auth | |
|---|---|---|
| `GET /health` | — | liveness + how many marks are set |
| `GET /status` | — | auth mode, token TTL, limits, where state lives |
| `GET /whoami` | token | the signer, and which modules it may write |
| `GET /marks` | — | every module that has set a mark |
| `GET /logo/{module}` | — | the mark to draw |
| `GET /logo/{module}/image` | — | uploaded bytes, CSP-hardened, immutable per `?v=` |
| `GET /logo/{module}/owner` | — | who may write it, and where that came from |
| `POST /logo/{module}` | **owner** | `{glyph}` · `{url}` · `{dataUrl}` · `{reset:true}` |
| `DELETE /logo/{module}` | **owner** | back to the cube |

`{module}` is a bare name (`build`) or a qualified one (`orbit/store`,
`core/store`). Module names in this protocol are path-derived and `core/` is
applied after `orbit/`, so a bare name that exists in both resolves to the
**core** one — qualify it to mean the other.

`/logo/_api/*` is the same API one path segment along, so a stored image `src`
resolves identically through the console's proxy and off the bare port.

---

## What a mark can be

| kind | what it is | limit |
|---|---|---|
| `cube` | nothing set — the protocol's own cube | the default |
| `glyph` | 1–4 characters | the cheapest possible logo |
| `url` | an image somebody else hosts | http(s), 2KB of URL |
| `image` | bytes stored here, served back from here | 512KB, PNG/JPEG/WEBP/GIF/SVG |

An uploaded SVG is markup that would run from this origin if someone opened its
URL directly, so the bytes go out under `default-src 'none'; sandbox` with
`nosniff`. It renders as an `<img>` and can do nothing else.

### The config.json mirror

A **short** mark (a glyph or a URL) is also written into the target module's
`config.json` `logo` field, so the fleet's catalogs can put the same mark on
their module cards; an upload mirrors as the path that serves it. The edit is
surgical — one line changed, inserted or dropped, the rest of the manifest byte
for byte identical — because other processes edit those files at the same time
(the registry rewrites `schema` on its own). It never throws: `~/.mod/logo/`
is the source of truth, and a read-only manifest must not fail a save.

---

## State

```
~/.mod/logo/
  owner.json                  this deployment's owner, if it was ever claimed
  marks/orbit/build.json      one module's mark
  marks/orbit/build.png       …and its uploaded bytes, if any
```

Nothing here is committed. Who owns a deployment and who co-owns a module are
deployment facts, not repository facts.

Importing a mark a module used to keep for itself:

```bash
python3 scripts/migrate_from_build.py --module build --write
```

It copies, never moves — the original stays where it was.

---

## Who uses it

**orbit/build** — the console's header mark. Build proxies the read (and caches
it, so a sleeping logo module cannot blank its own header) and forwards the
owner's signature on a write. It cannot mint one. See `build`'s
`src/app/src/lib/logoClient.ts` and `config.json` → `logo_module`.

Adding another consumer is two calls: `GET /logo/{you}` for the mark, and a
form that forwards whatever token the owner's wallet signed.

---

## Config

| env | default | |
|---|---|---|
| `LOGO_API_PORT` | `50760` | the API |
| `LOGO_APP_PORT` | `50761` | the console |
| `LOGO_DIR` | `~/.mod/logo` | state |
| `LOGO_TREE` | the repo's `mod/` | where modules are looked up |
| `LOGO_TOKEN_MAX_AGE` | `604800` | 7 days |
| `LOGO_MAX_IMAGE_BYTES` | `524288` | 512KB |
| `LOGO_PUBLIC_BASE` | `/logo/_api` | prefix an uploaded mark is served from |
| `LOGO_OPEN` | unset | **development only** — no gate |

---

## Tests

```bash
python3 -m pytest test -q          # 29 tests
```

They run against a throwaway state dir *and* a throwaway module tree, because
a test that can write `logo` into a real manifest is a test nobody will run
twice. The ones that matter are the gate: no token, a stranger's token, a
forged token, and the owner of one module trying to paint another.
