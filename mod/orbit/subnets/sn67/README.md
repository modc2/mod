# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003494 | +0.00% | -1.13% | -7.12% | 6,123 | 2,670 | 19.23 |

## Last 24h flow

142 trades by 19 coldkeys · 1 buys (0.93 τ) / 141 sells (13.85 τ) · net -12.92 τ

## News

- 2026-10-03 · commit · [Publish public Harnyx API docs and LLM-friendly references (#1643)](https://github.com/harnyx/harnyx/commit/886f030103e33c0b4bec73397116952b971eee6b) — harnyx/harnyx
- 2026-10-02 · commit · [chore(validator): bump repo-owned validator version to 20261002.post1](https://github.com/harnyx/harnyx/commit/f015c036a3986b1080a66067e641b825ba6d6559) — harnyx/harnyx
- 2026-10-02 · commit · [chore(validator): bump repo-owned validator version to 20261002.post0](https://github.com/harnyx/harnyx/commit/03366abd6f64ec156d3dc583e840f6bf1e31fd8b) — harnyx/harnyx
- 2026-10-02 · commit · [Add marketing consent preferences and Resend contact synchronization …](https://github.com/harnyx/harnyx/commit/f8c980074f8531bfa7ff33c2a236576a03d0a8ce) — harnyx/harnyx
- 2026-09-23 · commit · [chore(validator): bump repo-owned validator version to 20260923.post2](https://github.com/harnyx/harnyx/commit/a8fd4998d0aadda1e5099faeaacba9948a8de6b5) — harnyx/harnyx
- 2026-09-23 · commit · [Add MiMo-V2.6 hosted models to miner tools (#1609)](https://github.com/harnyx/harnyx/commit/2aceec3bea4cdc36d7099723a8272aeb9090e3dd) — harnyx/harnyx
- 2026-09-23 · commit · [chore(validator): bump repo-owned validator version to 20260923.post1](https://github.com/harnyx/harnyx/commit/96c18cf5993cfa877887c6ea344a4ecc17a6e648) — harnyx/harnyx
- 2026-09-23 · commit · [Document miner provider API key uniqueness (#1607)](https://github.com/harnyx/harnyx/commit/066921857f4a27e8b3e71e1db2c101cc0ab170d1) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
