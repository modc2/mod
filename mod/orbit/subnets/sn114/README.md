# sn114 — SOMA Є

Context compression layer delivered through MCP infrastructure

Bittensor subnet **114** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/DendriteHQ/SOMA) · [url](https://thesoma.ai) · [discord](https://discord.gg/durr4Sg6sM)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021487 | -0.31% | +8.41% | +11.13% | 51,186 | 9,310 | 2,932 |

## Last 24h flow

311 trades by 92 coldkeys · 155 buys (1,603 τ) / 156 sells (1,273 τ) · net 329.61 τ

## News

- 2026-10-06 · commit · [Merge pull request #227 from DendriteHQ/feat/call-jev](https://github.com/DendriteHQ/SOMA/commit/8d1e283fced1c4390913ee9fb9d178e00f8aad81) — DendriteHQ/SOMA
- 2026-10-05 · commit · [docs: add new prompting rules](https://github.com/DendriteHQ/SOMA/commit/d9e2f8a8333c7a235ba441acbeaa870c716391f7) — DendriteHQ/SOMA
- 2026-10-01 · commit · [fix(gateway): skip provider pin for systemone calls](https://github.com/DendriteHQ/SOMA/commit/c493fb3a1c2591b819a2b3f4ddb3c997808c560e) — DendriteHQ/SOMA
- 2026-10-01 · commit · [feat(sandbox): report compressor jev usage from benchmark metadata](https://github.com/DendriteHQ/SOMA/commit/268717f98fb5ca1e5a56e698b84759eefc5f54e7) — DendriteHQ/SOMA
- 2026-10-01 · commit · [feat(scoring): count jev input tokens in miner weighted tokens](https://github.com/DendriteHQ/SOMA/commit/f5d68e286468f372131ce783ea111c1b1f76d128) — DendriteHQ/SOMA
- 2026-10-01 · commit · [Merge pull request #226 from DendriteHQ/fix/sbx-space-clear](https://github.com/DendriteHQ/SOMA/commit/5d6ddb596d4260fe8aa71591cc6c5938d70d28f4) — DendriteHQ/SOMA
- 2026-10-01 · commit · [fix(sandbox): clean up abandoned Copilot run directories](https://github.com/DendriteHQ/SOMA/commit/fddf7fa7ac585186f1f87e1bc59f08e0ebd54861) — DendriteHQ/SOMA
- 2026-09-28 · commit · [Ignore agent changes to baked-in test files](https://github.com/DendriteHQ/SOMA/commit/e83a8df8b4b5e7f0ee48c4014f153ed611dc37b0) — DendriteHQ/SOMA

## Use

```bash
m subnets.sn114/info        # live identity + market (snapshot if bt is down)
m subnets.sn114/news        # scraped news
m subnets.sn114/trades      # 24h alpha tape
m subnets.sn114/daily       # daily candles
python3 orbit/subnets/sn114/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
