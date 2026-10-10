# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005079 | -0.00% | -1.25% | -5.01% | 1,220 | 468.8073 | 4.07 |

## Last 24h flow

4 trades by 4 coldkeys · 2 buys (0.55 τ) / 2 sells (1.22 τ) · net -0.67 τ

## News

- 2026-10-09 · commit · [Pool deliveries: claim-refusal backoff + claim_health(); validator --…](https://github.com/heroncovelabs/ormas-subnet/commit/c9a03f33fc5dbf5cf9a32cea5d1251aba8e8a311) — heroncovelabs/ormas-subnet
- 2026-10-09 · commit · [chore/requirements lock (#30)](https://github.com/heroncovelabs/ormas-subnet/commit/b3779155e6e4790d762d8899923ca4970d7078b3) — heroncovelabs/ormas-subnet
- 2026-10-09 · commit · [protected/miner/manifest.json: single admitted compose hash after the…](https://github.com/heroncovelabs/ormas-subnet/commit/d67205b17e8e36bbe6420abd6a17dea83ad0a20f) — heroncovelabs/ormas-subnet
- 2026-10-09 · commit · [Sync public_subnet: offers SDK, claim-refusal resilience, validator c…](https://github.com/heroncovelabs/ormas-subnet/commit/b6728d782d62f7de19f99248c3ba3c853de68219) — heroncovelabs/ormas-subnet
- 2026-10-07 · commit · [Sync public_subnet: doctor cells/hotkey/cap, /runners/me, --miner-id,…](https://github.com/heroncovelabs/ormas-subnet/commit/5530cb2d62b006060992c5dd6eb34334b55bbfb3) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [ormas-miner CLI and user-only installer (#25)](https://github.com/heroncovelabs/ormas-subnet/commit/38a02124e987c01ad54534f3e3871d7e3afea61b) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [fix: login confirms the save path and never echoes any part of the key](https://github.com/heroncovelabs/ormas-subnet/commit/b8a17c14be0f635c0f7fd7ff24f9d039588a27ee) — heroncovelabs/ormas-subnet
- 2026-10-06 · commit · [fix: installer PATH hint without quoted tilde (shellcheck SC2088)](https://github.com/heroncovelabs/ormas-subnet/commit/74ac7601a47451bbc0d07689e30092626ee82798) — heroncovelabs/ormas-subnet

## Use

```bash
m subnets.sn76/info        # live identity + market (snapshot if bt is down)
m subnets.sn76/news        # scraped news
m subnets.sn76/trades      # 24h alpha tape
m subnets.sn76/daily       # daily candles
python3 orbit/subnets/sn76/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
