# compute

**One interface to every decentralized compute market — and a way to actually
use what you rent.**

Eleven GPU markets and your own hardware, behind one vocabulary, one MCP server
and one console. Bittensor (Targon SN4, Lium SN51), Akash, Vast.ai, Clore,
Nosana, Cathedral (confidential Intel TDX), Prime Intellect, Polaris,
Hyperbolic, Shadeform, and whatever docker hosts you run yourself.

Renting is half the problem. The other half is that a box you just rented is a
stranger with an SSH port. `compute/deploy` finishes the job: it installs the
mod protocol on that box and hands you a shell, its module list and a call
button for every function on them — in the browser, over the same transport it
rented with, with nothing listening on a public interface.

```
m compute/search gpu=4090 max_usd_hr=1        # every market at once, cheapest first
m compute/quote id=vast:46655560 hours=4      # cost, plus what it costs elsewhere
m compute/rent  id=vast:46655560 confirm=1    # spends YOUR credits
m compute/instances                           # everything running + combined burn rate
m compute/stop  id=vast:14002931              # ends the billing

m compute/deploy docker=1                     # a mod container here, in ~15s
m compute/deploy id=vast:46655560 confirm=1   # rent one, install mod on it
m compute/node_sh id=n-73e368 cmd=nvidia-smi  # a shell on it
m compute/node_call id=n-73e368 mod=chain fn=balance
m compute/node_rm id=n-73e368 release=1       # container down, rental stopped
```

A real answer from that first command, live across four markets:

```
$0.136/hr  RTX 4090   vast:46655560     verified · reliability 99% · 3.9Gbps
$0.240/hr  RTX 4090   nosana:nvidia-4090-community   COMMUNITY · 10% network fee
$0.280/hr  RTX 4090   akash:rtx4090-24Gi             18 free across 6 providers
$0.300/hr  RTX 4090   lium:700fea51…                 tier secure · 1/1 free
```

## Why aggregate

Targon has 49 MCP tools, Lium has 20, Cathedral has its own. An agent that wants
a 4090 for two hours should not have to learn three APIs to find out one of them
is 2× the price. So the markets are normalized down to two nouns and eight verbs:

| noun | is | keyed by |
| --- | --- | --- |
| **offer** | something rentable, priced in **USD/hr** | `provider:ref` |
| **instance** | something rented and running, priced in USD/hr | `provider:ref` |

**search · quote · rent · instances · status · logs · exec · stop · balance** —
twenty-one MCP tools total (fourteen for the markets, seven for the nodes you
put on them), for every market, forever. New market = one adapter file; nothing
else in the module changes.

## Three rules

1. **One vocabulary.** Every market answers in the same shape. `compute_search`
   fans out in parallel and sorts by price across all of them.
2. **One caller's money.** Keys resolve per request, per provider: an explicit
   header, then the environment, then `~/.mod/compute/keys.json` (0600), then
   the sibling module's own key file. No house key, no cross-provider bleed, and
   no response ever contains a key. Chain-native markets need no key at all —
   and this module holds no wallet, so it can't sign for you either.
3. **Partial answers beat failures.** A dead or unkeyed market comes back inside
   `providers` with its reason; the rest of the answer still arrives.

## The markets

| provider | what | KYC | pays with | can |
| --- | --- | --- | --- | --- |
| `targon` | Bittensor SN4 GPUs and confidential VMs | none | TAO → credits | full lifecycle |
| `lium` | Bittensor SN51 GPU marketplace | none | crypto → credits | full lifecycle |
| `akash` | Cosmos deployment market, permissionless | none | AKT / USDC | price + **deploy plan** |
| `vast` | open marketplace, mostly consumer cards, cheapest | none | crypto or card | full lifecycle |
| `clore` | blockchain GPU marketplace, **whole catalog public** | none | CLORE / BTC / USDT | full lifecycle |
| `nosana` | Solana GPU grid | none | NOS | price + **job plan** |
| `cathedral` | attested Intel TDX / confidential GPU, signed receipts | email | credits | full lifecycle |
| `prime` | broker over decentralized + traditional clouds | email | crypto or card | full lifecycle |
| `polaris` | GPU cloud | email | crypto or card | full lifecycle |
| `hyperbolic` | independently operated GPUs, rented by the node | email | crypto or card | full lifecycle |
| `shadeform` | ~20 clouds behind one API — the **fiat benchmark** | account | card | full lifecycle |
| `local` | your own docker hosts, billed on-chain (`COMPUTE_LOCAL=1`) | none | market tokens | full lifecycle |

`compute_search kyc=none` restricts the fan-out to the permissionless half.

### The mod lane — three of these markets are also modules here

`targon`, `lium` and `cathedral` each ship as their own module on this box, with
their own console, MCP tools and key file. `compute_mods` reads those markets
**through those modules** and sets each answer beside what the direct lane reads
from the same upstream:

```
m compute/mods                      # every mod-fronted market, both lanes
m compute/mods mod=lium sample=10   # one of them, with offers
```

```
lium        via the mod  93 offers @ $0.25/hr   direct 93 @ $0.25/hr   agrees
cathedral   via the mod   5 offers @ $0.40/hr   direct  5 @ $0.40/hr   agrees
targon      via the mod  unreachable            direct  6 @ $0.09/hr
            /inventory → 410: the module is still on the Hub's v2 paths
```

That last row is the whole reason the lane exists: a sibling module pinned to an
API version its market retired looks perfectly healthy from the outside, and
only says so when something asks it the same question the upstream just
answered. The console shows it under **PROVIDERS › MODS**.

Ids in the mod lane are the upstream's own, so an offer found through a module
round-trips into `compute_quote` and `compute_rent` unchanged — the lane is a
view, never a second way to spend money.

### Akash and Nosana return plans, not rentals

Both are chain-native: renting means signing a transaction. This module holds no
wallet and will not pretend otherwise, so `compute_rent` on those returns a
ready-to-run **plan** — for Akash a complete SDL plus the four
`provider-services` commands; for Nosana the `@nosana/cli` line — which you or
your operator sign locally. Everything else about them (live pricing, capacity,
provider counts) works with no key and no wallet.

## Nodes — the rented box, running mod

A market hands you an SSH line and a bill. `compute/deploy` turns that into a
machine you can use, and keeps holding the rope afterwards.

```bash
m compute/deploy docker=1 name=demo          # a container here — nothing rented
m compute/deploy ssh=root@1.2.3.4            # a box you already have
m compute/deploy id=clore:18022 confirm=1    # rent it, then install into it
m compute/deploy instance=vast:14002931      # adopt a rental you already started
```

What that does, in about fifteen seconds on the lite profile:

```
probe    uname, python3, pip, git, docker, nvidia-smi          420ms
python   ensure python3 + pip                                  180ms
deps     9 wheels — everything `import mod` touches, no torch  6.4s
core     2.1 MiB of mod/core, streamed as a tar                380ms
ctl      modctl.py + an `m` shim that needs no pip install      90ms
verify   mod imports, 50 modules                               530ms
```

Then the node answers for itself:

```bash
m compute/nodes                                   # state, modules, burn rate
m compute/node_sh   id=n-73e368 cmd='nvidia-smi'  # a shell
m compute/node_mods id=n-73e368                   # what it can run
m compute/node_mods id=n-73e368 mod=chain         # that module's fns
m compute/node_call id=n-73e368 mod=chain fn=balance
m compute/node_push id=n-73e368 mod=compute       # send it a module you just wrote
m compute/node_rm   id=n-73e368 release=1         # tear down + stop the rental
```

Four decisions worth knowing:

- **The install ships from here.** The node gets a tar of *this checkout's*
  `mod/core` over the transport — no git on the box, no clone from GitHub, no
  drift between the module you are running and the one you deployed. The full
  tree is a gigabyte because orbit modules carry `node_modules`; core is 2 MiB,
  and `node_push` sends the rest one module at a time.
- **`lite` installs nine wheels, not `requirements.txt`.** They were found by
  installing one at a time into an empty `python:3.12-slim` until `import mod`
  stopped raising. `profile=full` installs the real requirements, torch
  included, and takes minutes — a rented GPU-hour spent downloading torch is a
  rented GPU-hour wasted.
- **Control is a file, not a port.** `modctl.py` sits on the node and speaks
  JSON over the same transport that installed it, answering between markers so
  a login banner or a pip warning can never be mistaken for the result. Nothing
  has to listen on a public interface for the browser to drive the box.
- **One transport interface, four ways in.** Local docker, SSH, a container
  inside an SSH host, or the provider's own exec API. Every step above is
  written once against `run(cmd)` and `put(path, bytes)`, so bootstrapping a
  Vast rental and bootstrapping the container on your desk are the same code.

The console's **NODES** tab is this with a terminal: pick a node, get a shell
with history, a three-column module browser (modules → functions → call), the
install trail with per-step timings, and a port list you can tunnel back to
your browser.

## Who is allowed

Reading a market is public — it spends nothing and reveals nothing. Everything
past that is owner-only, because renting spends the operator's credits,
`instances` shows what they are already paying for, and a node route runs a
shell on a machine they own.

| tier | routes | who |
| --- | --- | --- |
| open | info, health, providers, search, offer, quote, mods, tools | anyone |
| byok | instances, status, logs, balance, keys | anyone who brings their own provider key — it is their account being read |
| owner | rent, stop, exec, raw, set_key, **every `/node` route** | the token, or localhost |

```bash
m compute/token          # the bearer token, minted into ~/.mod/compute/server.secret
```

The console is handed the token automatically when you open it from the box
itself, and asks for it when you open it through the gateway. A proxied request
can never look like localhost: Caddy stamps `X-Forwarded-For` on everything it
forwards, and that is what the check reads.

## Money safety

Rentals keep billing after the agent stops paying attention, so:

- `compute_quote` is the only call that shows the **cross-market comparison** —
  run it before renting and you'll usually find the same card cheaper.
- `compute_rent` **refuses** anything estimated above `COMPUTE_CONFIRM_USD`
  (default $0.50) unless called again with `confirm=true`. It returns the quote
  instead.
- `compute_instances` reports the **combined burn rate** across every market.
- `compute_stop` is what ends billing. Suspending doesn't. Closing a tab doesn't.

## MCP

```bash
m compute/serve                    # api + console + mcp on :50510
m compute/mcp_config               # drop-in client config
python3 mcp.py                     # stdio, for Claude Code / Desktop
curl -sX POST :50510/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The console at `/compute`, the REST routes, the CLI fns and the MCP tools all
call the same `Hub`, so they cannot drift apart.

## Keys

```bash
m compute/set_key provider=lium key=…      # → ~/.mod/compute/keys.json (0600)
m compute/keys                             # which are set — never the keys
```

Also read (in this order): `x-lium-key:` / `x-compute-keys:` request headers,
`LIUM_API_KEY`-style env vars, then the sibling module's file
(`~/.mod/lium/api_key`, `~/.mod/targon/api_key`, `~/.mod/cathedral/keys.json`,
`~/.vast_api_key`) — so keys you already configured keep working here.

## Escape hatch

Aggregation always loses something. `compute_raw` calls a provider's own API
directly with that provider's key, for volumes, templates, receipts, subnet
weights and everything else the shared surface deliberately does not cover.

```bash
m compute/raw provider=lium path=/volumes
m compute/raw provider=akash path=/v1/network-capacity
```

## Layout

```
mod.py          the fns  →  hub / node
hub.py          fan-out, spend guard, id routing — knows no provider names
providers/
  base.py       Provider contract, offer/instance shapes, filters, key resolution
  targon.py lium.py akash.py vast.py clore.py nosana.py
  cathedral.py prime.py polaris.py hyperbolic.py shadeform.py local.py
mods.py         the mod lane: the same markets read through their own modules
node.py         transports, bootstrap, node registry — the rented box, running mod
modctl.py       what gets uploaded: JSON in, JSON out, on the far side
auth.py         open / byok / owner, and the token
mcp.py          21 tools + JSON-RPC 2.0 (stdio and Streamable HTTP)
api.py          REST + /mcp + console, stdlib only
console.html    zero-dependency browser console (market, nodes, terminal),
                drawn 8-bit: ten cabinet palettes, castle by default, CSS-only, no assets
test/           the adapter contract and the node contract, offline;
                public catalogs and one real container, live
```

No dependencies — Python standard library only.

## Notes

- Targon's Hub API moved **v2 → v3** (Aug 2026); every v2 path now answers 410.
  This module is on v3. The `targon` module itself is still pinned to v2 and its
  live calls are failing until it is repointed.
- The console is an 8-bit cabinet with **ten palettes**, **Castle** by
  default: Castle, Daylight, Night, Game Boy, Virtual, Hyrule, Brinstar, C64,
  Arcade, Neon. Pick one from **THEME** in the HUD, or press `T` to cycle; the
  choice is remembered in `localStorage` and `/compute?theme=neon` deep-links
  one. A theme is only a swatch list — one `data-theme` on `<html>` and every
  colour in the page, stone and torchlight included, comes back out of CSS
  vars, so nothing forks the markup. **SCANLINES**, in the same drawer, turns
  off the CRT glass.
- Everything decorative is CSS boxes — a moon and stars, a curtain wall across
  the page, and a keep with lit windows and two torches at the gate — so no
  image, font or sound file ships and the page still works offline (the pixel
  typeface is a Google Fonts progressive enhancement that falls back to
  monospace). Pressing anything can play a synthesised 8-bit blip; that is off
  until you hit **SOUND** in the HUD. The torches stop flickering under
  `prefers-reduced-motion`.
- `app/` still holds the old Next console for the on-chain host side (`local`
  provider). The aggregator's console is `console.html`, served by `api.py`.
