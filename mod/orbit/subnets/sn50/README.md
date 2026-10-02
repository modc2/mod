# sn50 — Synth ש

Predictive intelligence for financial markets and beyond

Bittensor subnet **50** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/mode-network/synth-subnet) · [url](https://synthdata.co)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004005 | +0.07% | +1.16% | +0.88% | 23,474 | 10,201 | 297.80 |

## Last 24h flow

93 trades by 69 coldkeys · 19 buys (176.54 τ) / 74 sells (118.93 τ) · net 57.61 τ

## News

- 2026-09-22 · release · [v1.13.0](https://github.com/synthdataco/synth-subnet/releases/tag/v1.13.0) — mode-network/synth-subnet
- 2026-09-14 · commit · [feat(validator): add a volatility CRPS term to the crypto-1h score (#…](https://github.com/synthdataco/synth-subnet/commit/2cfb24b29b745c37a143984e3e86e6c54f342293) — mode-network/synth-subnet
- 2026-09-03 · commit · [base miner: gzip-compress axon responses (#319)](https://github.com/synthdataco/synth-subnet/commit/a91a1f044f620d277cfa87eeb3b02d91ea79ad78) — mode-network/synth-subnet

## Use

```bash
m subnets.sn50/info        # live identity + market (snapshot if bt is down)
m subnets.sn50/news        # scraped news
m subnets.sn50/trades      # 24h alpha tape
m subnets.sn50/daily       # daily candles
python3 orbit/subnets/sn50/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
