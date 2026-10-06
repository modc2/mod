# sn114 — SOMA Є

Context compression layer delivered through MCP infrastructure

Bittensor subnet **114** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/DendriteHQ/SOMA) · [url](https://thesoma.ai) · [discord](https://discord.gg/durr4Sg6sM)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.020212 | +0.16% | -6.51% | +10.39% | 47,775 | 8,945 | 2,954 |

## Last 24h flow

380 trades by 169 coldkeys · 137 buys (1,273 τ) / 243 sells (1,618 τ) · net -344.94 τ

## News

- 2026-10-01 · commit · [Merge pull request #226 from DendriteHQ/fix/sbx-space-clear](https://github.com/DendriteHQ/SOMA/commit/5d6ddb596d4260fe8aa71591cc6c5938d70d28f4) — DendriteHQ/SOMA
- 2026-10-01 · commit · [fix(sandbox): clean up abandoned Copilot run directories](https://github.com/DendriteHQ/SOMA/commit/fddf7fa7ac585186f1f87e1bc59f08e0ebd54861) — DendriteHQ/SOMA
- 2026-09-28 · commit · [Ignore agent changes to baked-in test files](https://github.com/DendriteHQ/SOMA/commit/e83a8df8b4b5e7f0ee48c4014f153ed611dc37b0) — DendriteHQ/SOMA
- 2026-09-16 · commit · [docs: describe complexity incentive layers](https://github.com/DendriteHQ/SOMA/commit/83b828d695ae9551c1d4cd105a9965d29e778d75) — DendriteHQ/SOMA
- 2026-09-14 · commit · [Merge pull request #225 from DendriteHQ/dev](https://github.com/DendriteHQ/SOMA/commit/f6037f883c384e33d25b20a673bd98b22e82a39c) — DendriteHQ/SOMA

## Use

```bash
m subnets.sn114/info        # live identity + market (snapshot if bt is down)
m subnets.sn114/news        # scraped news
m subnets.sn114/trades      # 24h alpha tape
m subnets.sn114/daily       # daily candles
python3 orbit/subnets/sn114/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
