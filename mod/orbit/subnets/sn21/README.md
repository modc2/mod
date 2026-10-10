# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004823 | +0.20% | +1.58% | +38.95% | 29,708 | 8,939 | 5,020 |

## Last 24h flow

456 trades by 184 coldkeys · 176 buys (2,550 τ) / 280 sells (2,473 τ) · net 77.26 τ

## News

- 2026-10-09 · commit · [docs(rewards): self-mining section lists the operator-run miners (UID…](https://github.com/ippcteam/SN21-adtao/commit/585b5aa25a3e7cc17a713e5a1a4b6cd8d32fecfe) — ippcteam/SN21-adtao
- 2026-10-09 · release · [SN21 training data v4 (live basket shape)](https://github.com/ippcteam/SN21-adtao/releases/tag/training-v4-2026-10) — ippcteam/SN21-adtao
- 2026-10-09 · commit · [docs(training): v4 training data in the live daily basket shape](https://github.com/ippcteam/SN21-adtao/commit/95d50f617eeea040c57b6baaafb2d4f6d2f6cdb6) — ippcteam/SN21-adtao
- 2026-10-08 · commit · [fix(reference): the budget lean reads the live daily basket layout](https://github.com/ippcteam/SN21-adtao/commit/a375463741da5b1fad512f77325afc600ca994b9) — ippcteam/SN21-adtao
- 2026-10-08 · commit · [fix(scoring): copy groups agree on one earner, the earliest submission](https://github.com/ippcteam/SN21-adtao/commit/662fad784e2214fe21408db1aee2898a54c9e10c) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): /health in daily mode no longer echoes the loaded rel…](https://github.com/ippcteam/SN21-adtao/commit/67bac47190ffd16145733b703437017699dbf872) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): weekly prediction submissions are off unless SN21_WEE…](https://github.com/ippcteam/SN21-adtao/commit/0977912ce07fe4378e165feadd8e80dff6dfbaa6) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): /health reports daily mode once the weekly window has…](https://github.com/ippcteam/SN21-adtao/commit/afac3d0454cc56244a183f113333e35a35a3be42) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
