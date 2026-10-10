# sn75 — Hippius م

Blockchain-backed cloud: storage, VMs, and apps with unmatched transparency, trust, and power.

Bittensor subnet **75** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/thenervelab/thebrain) · [url](https://hippius.com/)

Fleet mods for this subnet: `hippius`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.017213 | +0.47% | +7.46% | +3.84% | 99,300 | 33,543 | 6,657 |

## Last 24h flow

373 trades by 141 coldkeys · 196 buys (3,835 τ) / 177 sells (2,730 τ) · net 1,105 τ

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
