# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003629 | -0.00% | +4.55% | +8.10% | 22,185 | 7,741 | 1,858 |

## Last 24h flow

123 trades by 65 coldkeys · 61 buys (1,014 τ) / 62 sells (842.71 τ) · net 171.45 τ

## News

- 2026-09-24 · release · [SN21 rich training data v3 (slice 2)](https://github.com/ippcteam/SN21-adtao/releases/tag/training-v3-2026-09) — ippcteam/SN21-adtao
- 2026-09-24 · commit · [docs: training data v3 slice (windows 5-14 Aug) and the slice release…](https://github.com/ippcteam/SN21-adtao/commit/476b4f80439455680c1a75746ab252c6cb7a0b61) — ippcteam/SN21-adtao
- 2026-09-21 · commit · [verify: the grouping recheck narrows to the same rows the run read](https://github.com/ippcteam/SN21-adtao/commit/0e977e614276960cc79cb0eed40eb883c33d218f) — ippcteam/SN21-adtao
- 2026-09-21 · commit · [lineage: the audit carries the rows the signals read](https://github.com/ippcteam/SN21-adtao/commit/6b24e0c1157238edf7d1d71ce26fc772969f4486) — ippcteam/SN21-adtao
- 2026-09-21 · commit · [lineage: compare each hotkey on the rows its current model produced](https://github.com/ippcteam/SN21-adtao/commit/85ed3c894e0ac8e5a34db02776ea7cf9782ab004) — ippcteam/SN21-adtao
- 2026-09-21 · commit · [verdict feed: read the gate numbers and the corpus from the intake re…](https://github.com/ippcteam/SN21-adtao/commit/af3fcdd53e5d3774451534a17e13c99c570688ba) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
