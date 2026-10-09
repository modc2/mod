# sn114 — SOMA Є

Context compression layer delivered through MCP infrastructure

Bittensor subnet **114** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/DendriteHQ/SOMA) · [url](https://thesoma.ai) · [discord](https://discord.gg/durr4Sg6sM)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021058 | +0.59% | -2.00% | -1.88% | 50,357 | 9,261 | 2,501 |

## Last 24h flow

270 trades by 109 coldkeys · 154 buys (1,153 τ) / 116 sells (1,291 τ) · net -137.53 τ

## News

- 2026-10-08 · commit · [Merge pull request #228 from DendriteHQ/dev](https://github.com/DendriteHQ/SOMA/commit/fc7ecf46fb7eee19926102902d018ef27b5c1338) — DendriteHQ/SOMA
- 2026-10-08 · commit · [feat(frontend): report 0 jev tokens for runs without jev calls](https://github.com/DendriteHQ/SOMA/commit/c7d7b4b741cef4a6512d9b2b6796da6cfd76db86) — DendriteHQ/SOMA
- 2026-10-08 · commit · [Lower Jev input token weight to 0.2](https://github.com/DendriteHQ/SOMA/commit/790376f75996164c3b036611884f8b7fbcb2a00c) — DendriteHQ/SOMA
- 2026-10-06 · commit · [Merge pull request #227 from DendriteHQ/feat/call-jev](https://github.com/DendriteHQ/SOMA/commit/8d1e283fced1c4390913ee9fb9d178e00f8aad81) — DendriteHQ/SOMA
- 2026-10-05 · commit · [docs: add new prompting rules](https://github.com/DendriteHQ/SOMA/commit/d9e2f8a8333c7a235ba441acbeaa870c716391f7) — DendriteHQ/SOMA
- 2026-10-01 · commit · [fix(gateway): skip provider pin for systemone calls](https://github.com/DendriteHQ/SOMA/commit/c493fb3a1c2591b819a2b3f4ddb3c997808c560e) — DendriteHQ/SOMA
- 2026-10-01 · commit · [feat(sandbox): report compressor jev usage from benchmark metadata](https://github.com/DendriteHQ/SOMA/commit/268717f98fb5ca1e5a56e698b84759eefc5f54e7) — DendriteHQ/SOMA
- 2026-10-01 · commit · [feat(scoring): count jev input tokens in miner weighted tokens](https://github.com/DendriteHQ/SOMA/commit/f5d68e286468f372131ce783ea111c1b1f76d128) — DendriteHQ/SOMA

## Use

```bash
m subnets.sn114/info        # live identity + market (snapshot if bt is down)
m subnets.sn114/news        # scraped news
m subnets.sn114/trades      # 24h alpha tape
m subnets.sn114/daily       # daily candles
python3 orbit/subnets/sn114/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
