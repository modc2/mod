# sn50 — Synth ש

Predictive intelligence for financial markets and beyond

Bittensor subnet **50** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/mode-network/synth-subnet) · [url](https://synthdata.co)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004790 | +0.00% | -0.29% | +18.66% | 28,212 | 11,169 | 3,016 |

## Last 24h flow

348 trades by 142 coldkeys · 173 buys (1,497 τ) / 175 sells (1,513 τ) · net -16.07 τ

## News

- 2026-10-05 · commit · [chore(deps): bump virtualenv from 21.4.3 to 21.7.13 (#329)](https://github.com/synthdataco/synth-subnet/commit/d7693a264979b2808e6a8695e304ed1822037c8a) — mode-network/synth-subnet
- 2026-10-05 · commit · [chore(deps): bump gitpython from 3.1.59 to 3.1.62 (#331)](https://github.com/synthdataco/synth-subnet/commit/cbfc2380e0528eecab3d697a8c16c1d0509c4e59) — mode-network/synth-subnet
- 2026-10-05 · commit · [chore(deps): bump urllib3 from 2.7.0 to 2.8.0 (#330)](https://github.com/synthdataco/synth-subnet/commit/78ecb48be15bb85e9d43ca884dfe66b654a233f4) — mode-network/synth-subnet
- 2026-10-05 · commit · [perf(validator): reuse dendrite process pool, as_completed, drop per-…](https://github.com/synthdataco/synth-subnet/commit/2fe68b7dfc9bebb790eec72483e10ea23bcc71b7) — mode-network/synth-subnet
- 2026-10-05 · commit · [chore(deps): bump h2 from 4.3.0 to 4.4.1 (#317)](https://github.com/synthdataco/synth-subnet/commit/d28a233687ce0346617787bd5b9fe250f4111ca4) — mode-network/synth-subnet
- 2026-09-22 · release · [v1.13.0](https://github.com/synthdataco/synth-subnet/releases/tag/v1.13.0) — mode-network/synth-subnet
- 2026-09-14 · commit · [feat(validator): add a volatility CRPS term to the crypto-1h score (#…](https://github.com/synthdataco/synth-subnet/commit/2cfb24b29b745c37a143984e3e86e6c54f342293) — mode-network/synth-subnet

## Use

```bash
m subnets.sn50/info        # live identity + market (snapshot if bt is down)
m subnets.sn50/news        # scraped news
m subnets.sn50/trades      # 24h alpha tape
m subnets.sn50/daily       # daily candles
python3 orbit/subnets/sn50/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
