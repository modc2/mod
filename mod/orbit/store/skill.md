# store — share a picture publicly, or once

Two ways to hand somebody an image. They are deliberately not the same thing,
and picking the wrong one is the mistake this module exists to make hard.

| | audience | how long | undo |
|---|---|---|---|
| **publish** | anyone who ever sees the link | forever | unpublishing stops *new* readers only |
| **grant** | the first person to open it | N seconds | revoke, until someone spends it |

Default to a grant. Publish only when a permanent open link is what was
actually asked for.

## Start here

```bash
cd /root/mod/mod/orbit/store
python3 mod.py share photo.jpg for=5m    # store it, mint a code, draw the QR
python3 mod.py help                      # every verb, in one screen
```

`share` is the whole errand in one call. It stores the file if it is new, mints
a code good for one fetch, and prints the QR — and it never publishes.

## Naming things

No caller has to type a sha256. Anywhere a picture is named:

| write | means |
|---|---|
| `e54c50db…` (64 hex) | the id |
| `e54c` | any unique prefix, 4+ characters |
| `sunset.png` | the name you stored it under |
| `latest` | the most recent one (`last`, `newest`) |

Two matches is an error naming both, never a guess. Durations are `30s`, `5m`,
`2h`, `1d`, or a bare number of seconds; `ttl_seconds=` still means seconds.

A **code** is different: pass the whole thing. Prefixes resolve only for codes
you minted, and only off the public routes — the code is the credential.

## The link you hand over is a page, not the bytes

```
/v/<code>   the page about a one-time code   — opening it claims NOTHING
/g/<code>   the bytes behind it              — fetching them BURNS it
/p/<id>     the page about a published image
/i/<id>     the published bytes
```

Send the **page** (`page_url`). It is safe to paste into a chat: a preview bot
or a prefetch renders it without spending anything, and the claim happens when
a person presses the button. Never paste `bytes_url` — whatever touches it
first spends the code.

## As an agent (MCP)

Eighteen tools on the API port, no second process.

```bash
curl -s http://127.0.0.1:50670/mcp/schema          # tools, prompts, resources
python3 src/mcp.py                                 # stdio transport
```

- **`store_share`** — the one-call errand: store if new, mint a code, stay private.
- **`store_view`** — look at a picture. Returns the image, not a description.
- **`store_peek`** — is this code still good? Spends nothing.
- **`store_claim`** — redeem it. **This spends it**, including for you. Only
  call it when the intent is to receive the picture. It returns the bytes as an
  image block, because by then that response is the only copy anyone gets.
- **`store_publish`** — permanent, public, no credential. Not undoable for
  copies already made.

Resources: `store://docs`, `store://docs/<section>`, `store://image/<id>`.
Prompts: `share_with_one_person`, `publish_forever`.

## Documentation

One dictionary in `src/docs.py`, rendered four ways, so a route that changes is
wrong in one place rather than right in one and stale in three.

```bash
python3 mod.py docs                        # as data
curl http://127.0.0.1:50670/docs           # as JSON
open http://127.0.0.1:50671/store/docs     # as a page
open http://127.0.0.1:50671/store/#docs    # the console's DOCS tab
```

## Reaching it

API `:50670`, console `:50671/store`, both loopback, `route: false`. The public
read path has no credential on it at all, so routing this to the gateway
publishes every image anyone on the box has published — turn it on deliberately.

**`m.mod('store')` is NOT this module.** Names are path-derived and `core`
overwrites `orbit`, so that resolves to `core/store` and always will. Use the
HTTP ports or `python3 mod.py <fn>`. State lives in `~/.mod/store-share/`,
never `~/.mod/store/`, which is `core/store`'s live database.

## What it refuses

png · jpeg · gif · webp · bmp, decided by sniffing magic bytes and never by the
filename or the Content-Type the uploader claims. **SVG is refused outright**:
every other image format is inert data, but SVG is a document that can carry
script and this is served from an origin the whole fleet shares.

## Tests

```bash
python3 -m pytest tests/ -q      # 84 passing
```
