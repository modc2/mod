# sn100 — Cortex დ

The open alternative to frontier AI | Efficient autonomous research

Bittensor subnet **100** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/CortexLM/cortex) · [url](https://network.cortex.foundation)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003003 | -0.00% | -4.43% | -17.57% | 8,402 | 2,206 | 285.65 |

## Last 24h flow

70 trades by 36 coldkeys · 29 buys (117.56 τ) / 41 sells (166.21 τ) · net -48.66 τ

## News

- 2026-10-19 · commit · [Merge pull request #320 from CortexLM/chore/bump-aiohttp-3.14.4](https://github.com/CortexLM/cortex/commit/85b6f8a60117d6d57f71e7b802990c9830ab0ea1) — CortexLM/cortex
- 2026-10-19 · commit · [fix(deps): bump aiohttp to 3.14.4 for CVE-2026-111064 and GHSA-2g87-p…](https://github.com/CortexLM/cortex/commit/e9fb2a5f29b1dedab36c567b1c519a7a71206747) — CortexLM/cortex
- 2026-10-19 · commit · [Merge pull request #319 from CortexLM/forge/no-signatures](https://github.com/CortexLM/cortex/commit/cd5d4b5d08b95b28518d7f47a23802b7d0edb136) — CortexLM/cortex
- 2026-10-19 · commit · [docs(validator): cover both profile version caps and restart wording](https://github.com/CortexLM/cortex/commit/b50cb7ac9cccd78eb523a03ea8c44edda742904f) — CortexLM/cortex
- 2026-10-19 · commit · [test(validator): skip read-only watermark reset cases as root](https://github.com/CortexLM/cortex/commit/71b0cca4ec0d07a16b7f9480175cdf6611e4ffde) — CortexLM/cortex
- 2026-10-16 · commit · [fix(challenges): raise get_weights timeout to 180 seconds (#318)](https://github.com/CortexLM/cortex/commit/602ea0298b2f18cccee219dd13ec290eb902b622) — CortexLM/cortex
- 2026-10-08 · commit · [docs(repo): remove retired products, add readme and validator script …](https://github.com/CortexLM/cortex/commit/df0ce4a63b334a6205d7bf1f8bea61424c90e29c) — CortexLM/cortex
- 2026-09-28 · commit · [feat(master): send the completed epoch's chain time to challenges (#315)](https://github.com/CortexLM/cortex/commit/d738424c09213af3634b24a1ba1a0fddb7a9379d) — CortexLM/cortex

## Use

```bash
m subnets.sn100/info        # live identity + market (snapshot if bt is down)
m subnets.sn100/news        # scraped news
m subnets.sn100/trades      # 24h alpha tape
m subnets.sn100/daily       # daily candles
python3 orbit/subnets/sn100/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
