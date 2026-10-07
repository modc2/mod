# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005343 | -0.00% | -0.08% | -13.34% | 1,168 | 480.8408 | 6.67 |

## Last 24h flow

7 trades by 6 coldkeys · 4 buys (3.23 τ) / 3 sells (0.19 τ) · net 3.04 τ

## News

- 2026-10-06 · commit · [ormas-miner CLI and user-only installer (#25)](https://github.com/heroncovelabs/ormas-subnet/commit/38a02124e987c01ad54534f3e3871d7e3afea61b) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [fix: login confirms the save path and never echoes any part of the key](https://github.com/heroncovelabs/ormas-subnet/commit/b8a17c14be0f635c0f7fd7ff24f9d039588a27ee) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [fix: installer PATH hint without quoted tilde (shellcheck SC2088)](https://github.com/heroncovelabs/ormas-subnet/commit/74ac7601a47451bbc0d07689e30092626ee82798) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [Adds the public `ormas-miner` command and a user-only installer, so a…](https://github.com/heroncovelabs/ormas-subnet/commit/4161cd5c4b92e8cd7d75adb9f9a9a52d546220b4) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [Security and FAQ contact: ops@ormas.ai (#24)](https://github.com/heroncovelabs/ormas-subnet/commit/e718c1a3911670859dabbf2b296aad9dcb87a992) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [Mirror public_subnet @ f04e40561: api.ormas.ai runs operator-run-v3; …](https://github.com/heroncovelabs/ormas-subnet/commit/21e7f0e980f6bc402461f67dce282482c759897f) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [Mirror public_subnet @ 57f04a277: voluntary counts-only effort block,…](https://github.com/heroncovelabs/ormas-subnet/commit/7ca521fec20f8b9bd5e8ec4b72b020a91a9b5cd7) — heroncovelabs/ormas-subnet
- 2026-10-05 · commit · [MINER_TERMS §3: publish rate version earned-bid-2x-v1 (2x from 2026-1…](https://github.com/heroncovelabs/ormas-subnet/commit/8315f51dfa677bbe9c08f039450f274f97af94eb) — heroncovelabs/ormas-subnet

## Use

```bash
m subnets.sn76/info        # live identity + market (snapshot if bt is down)
m subnets.sn76/news        # scraped news
m subnets.sn76/trades      # 24h alpha tape
m subnets.sn76/daily       # daily candles
python3 orbit/subnets/sn76/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
