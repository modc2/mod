# sn75 — Hippius م

Blockchain-backed cloud: storage, VMs, and apps with unmatched transparency, trust, and power.

Bittensor subnet **75** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/thenervelab/thebrain) · [url](https://hippius.com/)

Fleet mods for this subnet: `hippius`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.017135 | +0.00% | +0.63% | +10.04% | 99,403 | 33,538 | 4,741 |

## Last 24h flow

376 trades by 179 coldkeys · 168 buys (2,342 τ) / 208 sells (2,306 τ) · net 36.62 τ

## News

- 2026-10-05 · commit · [Merge pull request #63 from thenervelab/feat/marketplace-buy-credits](https://github.com/thenervelab/thebrain/commit/530ef9997385b54fa450aab4e5835dd3865a18de) — thenervelab/thebrain
- 2026-10-05 · commit · [feat(marketplace): buy_credits — users pay native tokens for credits …](https://github.com/thenervelab/thebrain/commit/b4cd2511199e009c2147f2f2bfa343ce4b9b060b) — thenervelab/thebrain
- 2026-09-25 · commit · [Merge pull request #60 from thenervelab/feat/marketplace-compute-usage](https://github.com/thenervelab/thebrain/commit/6c42aa9da10027cfdf89527e805390000761905b) — thenervelab/thebrain
- 2026-09-24 · commit · [fix(marketplace): price compute billing proof size at FRAME's unbound…](https://github.com/thenervelab/thebrain/commit/dc8f2b5bc6baebb89f71bf5b9bba31ff5cc61176) — thenervelab/thebrain
- 2026-09-24 · commit · [feat(marketplace): charge hourly compute usage once per account and p…](https://github.com/thenervelab/thebrain/commit/cc94609226a34ddcb4298a672bb2e59ad7753bf7) — thenervelab/thebrain

## Use

```bash
m subnets.sn75/info        # live identity + market (snapshot if bt is down)
m subnets.sn75/news        # scraped news
m subnets.sn75/trades      # 24h alpha tape
m subnets.sn75/daily       # daily candles
python3 orbit/subnets/sn75/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
