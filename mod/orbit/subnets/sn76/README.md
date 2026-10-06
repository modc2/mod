# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005348 | -0.01% | -4.51% | -7.15% | 1,131 | 481.0352 | 12.29 |

## Last 24h flow

9 trades by 8 coldkeys · 2 buys (0.51 τ) / 7 sells (2.42 τ) · net -1.91 τ

## News

- 2026-10-06 · commit · [Security and FAQ contact: ops@ormas.ai (#24)](https://github.com/heroncovelabs/ormas-subnet/commit/e718c1a3911670859dabbf2b296aad9dcb87a992) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [Mirror public_subnet @ f04e40561: api.ormas.ai runs operator-run-v3; …](https://github.com/heroncovelabs/ormas-subnet/commit/21e7f0e980f6bc402461f67dce282482c759897f) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [Mirror public_subnet @ 57f04a277: voluntary counts-only effort block,…](https://github.com/heroncovelabs/ormas-subnet/commit/7ca521fec20f8b9bd5e8ec4b72b020a91a9b5cd7) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [MINER_TERMS §3: publish rate version earned-bid-2x-v1 (2x from 2026-1…](https://github.com/heroncovelabs/ormas-subnet/commit/8315f51dfa677bbe9c08f039450f274f97af94eb) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [sync: public_subnet @ tensorbox-spec 6cdf6088b5 — catalog 2026-10-04.…](https://github.com/heroncovelabs/ormas-subnet/commit/29acff137f8bb2238e4b07c2ce415e3f1846a606) — heroncovelabs/ormas-subnet
- 2026-10-03 · commit · [docs: queue route, offers and limit settlement are live since gateway…](https://github.com/heroncovelabs/ormas-subnet/commit/876a546d6ed78a91c7b10dd61e06b0eaa598dacb) — heroncovelabs/ormas-subnet
- 2026-10-03 · commit · [sync: public_subnet @ tensorbox-spec 29170e22ee — firm-or-limit offer…](https://github.com/heroncovelabs/ormas-subnet/commit/bf379667556e93ba23e65b540cbad427edc1b86a) — heroncovelabs/ormas-subnet
- 2026-09-16 · commit · [fix(skeleton): renew through the whole lease, retry a transient compl…](https://github.com/heroncovelabs/ormas-subnet/commit/b5bd859fdcac9dbd84980a8d3779052deeb43e4a) — heroncovelabs/ormas-subnet

## Use

```bash
m subnets.sn76/info        # live identity + market (snapshot if bt is down)
m subnets.sn76/news        # scraped news
m subnets.sn76/trades      # 24h alpha tape
m subnets.sn76/daily       # daily candles
python3 orbit/subnets/sn76/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
