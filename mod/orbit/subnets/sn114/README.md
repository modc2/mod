# sn114 — SOMA Є

Context compression layer delivered through MCP infrastructure

Bittensor subnet **114** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/DendriteHQ/SOMA) · [url](https://thesoma.ai) · [discord](https://discord.gg/durr4Sg6sM)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.022474 | — | -1.63% | -5.29% | 55,790 | 10,025 | 630.28 |

## Last 24h flow

101 trades by 60 coldkeys · 36 buys (227.00 τ) / 65 sells (348.17 τ) · net -121.18 τ

## News

- 2026-10-16 · commit · [update scoring.md docs](https://github.com/DendriteHQ/SOMA/commit/f46fb54ce56dfd67ecfbb369bb2822275d728458) — DendriteHQ/SOMA
- 2026-10-13 · commit · [Merge pull request #229 from DendriteHQ/fix/soma-task-eval-resource-l…](https://github.com/DendriteHQ/SOMA/commit/dec6330708f9fb0e004ae47538bbcfd0350e7f33) — DendriteHQ/SOMA
- 2026-10-13 · commit · [fix(validator): bound SOMA task eval container to N visible CPUs](https://github.com/DendriteHQ/SOMA/commit/ccd4c6b50e194473da6d91485a86e061b65d24df) — DendriteHQ/SOMA
- 2026-10-08 · commit · [Merge pull request #228 from DendriteHQ/dev](https://github.com/DendriteHQ/SOMA/commit/fc7ecf46fb7eee19926102902d018ef27b5c1338) — DendriteHQ/SOMA
- 2026-10-08 · commit · [feat(frontend): report 0 jev tokens for runs without jev calls](https://github.com/DendriteHQ/SOMA/commit/c7d7b4b741cef4a6512d9b2b6796da6cfd76db86) — DendriteHQ/SOMA
- 2026-10-08 · commit · [Lower Jev input token weight to 0.2](https://github.com/DendriteHQ/SOMA/commit/790376f75996164c3b036611884f8b7fbcb2a00c) — DendriteHQ/SOMA
- 2026-10-06 · commit · [Merge pull request #227 from DendriteHQ/feat/call-jev](https://github.com/DendriteHQ/SOMA/commit/8d1e283fced1c4390913ee9fb9d178e00f8aad81) — DendriteHQ/SOMA
- 2026-10-05 · commit · [docs: add new prompting rules](https://github.com/DendriteHQ/SOMA/commit/d9e2f8a8333c7a235ba441acbeaa870c716391f7) — DendriteHQ/SOMA

## Use

```bash
m subnets.sn114/info        # live identity + market (snapshot if bt is down)
m subnets.sn114/news        # scraped news
m subnets.sn114/trades      # 24h alpha tape
m subnets.sn114/daily       # daily candles
python3 orbit/subnets/sn114/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
