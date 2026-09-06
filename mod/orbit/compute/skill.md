---
name: compute
description: Rent GPUs from any decentralized market through one interface — search every provider at once (Bittensor Targon/Lium, Akash, Vast.ai, Clore, Nosana, Aleph Cloud, Cathedral confidential TDX, Prime Intellect, Polaris, Hyperbolic, RunPod, Fluence, Shadeform, your own hosts), compare prices, rent, watch and stop — then turn the rented box into a mod protocol node you can shell into, list modules on, and call functions on from the browser. Use when asked to find, price, rent or stop compute anywhere, for no-KYC / crypto-paid GPUs, or to set up and drive a remote container.
type: orbit-module
---

# compute

One MCP tool layer over eleven compute markets plus your own hardware, and a
node layer that installs the mod protocol on what you rent. Everything is
normalized: offers and rentals share one shape, priced in **USD/hr**, addressed
as **`provider:ref`** (`vast:46655560`, `clore:18022`). Nodes are `n-xxxxxx`.

Port **50510** — API, console (`/compute`) and MCP (`POST /mcp`) share it.

## Money rule

`compute_rent` spends the caller's real credits and keeps spending until
`compute_stop`. Before renting: run `compute_quote` and **say the hourly number
out loud**, including the cheaper alternative if there is one. Estimates above
$0.50 return `needs_confirm` instead of renting — pass `confirm=true` only when
the human has seen the price. After the work is done, `compute_stop`; nothing
else ends the meter.

## The loop

```
m compute/providers kyc=none        # who is reachable, who needs a key
m compute/search gpu=H100 min_gpus=8 max_usd_hr=20
m compute/quote id=vast:46095107 hours=6
m compute/rent  id=vast:46095107 hours=6 confirm=1
m compute/instances                 # everything running + combined burn rate
m compute/logs id=vast:14002931
m compute/stop id=vast:14002931
```

Search filters: `gpu` (loose — `4090` finds `NVIDIA GeForce RTX 4090`),
`min_gpus`, `min_vram_gb`, `min_usd_hr`/`max_usd_hr` (a price band — a floor
is how you skip the $0.001 junk tier), `region`, `provider`, `kyc`,
`kind` (gpu|cpu|confidential|job|storage|all), `sort` (price|vram|gpus), `limit` (up to 2000), `raw=0` to drop each offer's provider payload — what the console's distribution view uses to pull the whole catalogue in one call.

`compute_search` never fails because one market is down — check the `providers`
map in the result for who answered and who didn't, and say so if it matters.

## Where it is

`compute_map` is `compute_search` answered as places instead of rows: one point
per city or country, with the offer count, the markets there and the
cheapest/median price. Use it to pick a jurisdiction or to sit near your data,
then re-run `compute_search` with that `region`.

```
m compute/map gpu=4090 kyc=none
```

- `precision` on a point says what it is worth: `city`, `state`, `region` (a
  cloud's own anchor) or `country` (the market only published a country code).
- `unplaced` counts the offers whose market publishes no location at all —
  RunPod, Nosana, Aleph, Akash, Polaris, Cathedral and Targon sell a catalogue,
  not a located machine. They are never placed on a guess.
- Reading the map spends nothing and needs no key.

In the console this is the **WHERE** panel on the MARKET tab: click a square to
filter every other panel to that place.

## Markets

| provider | KYC | pays with | notes |
| --- | --- | --- | --- |
| `vast` | none | crypto/card | cheapest by far; consumer cards; search needs no key |
| `clore` | none | CLORE/BTC/USDT | whole catalog public, ~1300 rigs; on-demand price only (spot can be outbid) |
| `lium` | none | crypto | Bittensor SN51; browsing public; rent needs key + SSH key |
| `targon` | none | TAO | Bittensor SN4; pricing public; **Hub API is v3** |
| `akash` | none | AKT/USDC | permissionless; `rent` returns an **SDL deploy plan** |
| `nosana` | none | NOS | Solana; `rent` returns a **`@nosana/cli` job plan** |
| `aleph` | none | ALEPH | Aleph Cloud; fixed tiers priced in ALEPH streamed per second, USD via live token rate; `rent` returns an **aleph-client plan** |
| `cathedral` | email | credits | attested TDX + confidential GPU, signed receipts; some shapes are **per execution, not hourly** |
| `prime` | email | crypto/card | even its catalog needs a key |
| `polaris` | email | crypto/card | |
| `hyperbolic` | email | crypto/card | rented per node inside a cluster: ids are `cluster/node`; catalog needs a key |
| `runpod` | email | card/crypto | both lanes quoted: ids are `community/<gpu>` (marketplace hosts) or `secure/<gpu>`; catalog public |
| `fluence` | email | USDC/card | decentralized **CPU** VMs; even the catalog needs a key; `rent` returns the draft→provision plan |
| `shadeform` | account | card | fiat benchmark — excluded by `kyc=none` |
| `local` | none | market tokens | your docker hosts; needs `COMPUTE_LOCAL=1` |

For "no KYC" / "crypto only" asks, pass `kyc=none`. Vast, Akash and Nosana are
the answers that need no account at all — the last two need a funded wallet
instead, and this module holds none, so it hands back a plan to sign rather than
signing anything.

## The mod lane

`targon`, `lium` and `cathedral` also run as their own modules here.
`m compute/mods` (MCP: `compute_mods`) reads those three markets *through those
modules* and prints each answer beside the direct-to-upstream one, so a module
that has drifted from the market it fronts is visible — today `targon`'s module
is still on the Hub's retired v2 paths and answers 410, while this module's
direct lane reads v3 fine. Use it when asked "is the lium/targon/cathedral
module working", or before trusting a number that came from one of them.
Renting always goes direct: mod-lane ids are the upstream's own.

## Nodes — setting up a container and driving it

A rental is not a machine you can use until something installs on it. That is
`compute_deploy`, and it works four ways:

```
m compute/deploy docker=1                   # a container here — rents nothing
m compute/deploy ssh=root@1.2.3.4           # a box you can already reach
m compute/deploy id=clore:18022 confirm=1   # rent that offer, then install
m compute/deploy instance=vast:14002931     # adopt a rental already running
```

`profile=lite` (default) installs nine wheels + mod core in about 15s.
`profile=full` also installs requirements.txt (torch) and takes minutes — only
ask for it when the work actually needs torch on the node.

Then drive it. None of this needs an open port on the box; it all rides the
transport that installed it (docker exec, SSH, or the provider's exec API):

```
m compute/nodes                                   # state, mod version, burn rate
m compute/node_sh   id=n-73e368 cmd='nvidia-smi'
m compute/node_mods id=n-73e368                   # modules on that node
m compute/node_mods id=n-73e368 mod=chain         # that module's fns
m compute/node_call id=n-73e368 mod=chain fn=balance
m compute/node_push id=n-73e368 mod=compute       # send a local module over
m compute/node_rm   id=n-73e368 release=1         # tear down AND stop the rental
```

`node_rm` without `release=1` leaves the rental billing. Say that out loud when
you tear something down.

A freshly deployed node has **mod core only** — `node_push` is how anything
else gets there, and it sends the local directory as it is on disk, not a git
checkout. A `lite` node has no torch, no web3 and no pm2, so `key` and `chain`
fns that need them will fail there; that is the profile trade, not a bug.

## Who is allowed

Search, providers, quote, offer and the mod lane are public. Renting,
`instances`, `balance` and **every node route** are owner-only: they need
`Authorization: Bearer $(m compute/token)` or a request from localhost that did
not come through the gateway. Over MCP, stdio is always the owner; HTTP needs
the token.

## Keys

BYOK, per provider, never shared. Resolution order: `x-<provider>-key` request
header → env (`LIUM_API_KEY`, `VAST_API_KEY`, `TARGON_API_KEY`, …) →
`~/.mod/compute/keys.json` → the sibling module's file (`~/.mod/lium/api_key`,
`~/.vast_api_key`, …). Set one with
`m compute/set_key provider=vast key=… ` (written 0600, off-tree, never
committed). No route or tool ever returns a key.

Public reads that need no key at all: search on vast, clore, lium, targon,
akash, nosana, aleph, cathedral, polaris, runpod, shadeform.

## When the shared surface isn't enough

`m compute/raw provider=lium path=/volumes` calls that provider's own API with
that provider's key — volumes, templates, receipts, subnet weights, anything
the normalized verbs deliberately skip. For deep single-market work, the
dedicated modules (`targon`, `lium`, `cathedral`) still exist and go deeper.

## Serving

`m compute/serve` runs API + console + MCP under pm2 as `compute-api`. The
console's NODES tab is a terminal, a module browser and the install trail for
every node.
`m compute/mcp_config` prints client config (stdio `python3 mcp.py`, or HTTP
`http://localhost:50510/mcp`). Zero dependencies — stdlib only.
