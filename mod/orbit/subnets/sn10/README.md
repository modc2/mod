# sn10 — Pareton Ⱂ

The Intelligence Layer for AI Inference

Bittensor subnet **10** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/Pareton-ai/pareton) · [url](https://www.pareton.ai/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006228 | -0.00% | +2.92% | +1.61% | 37,856 | 16,264 | 1,355 |

## Last 24h flow

75 trades by 33 coldkeys · 37 buys (793.58 τ) / 38 sells (559.97 τ) · net 233.61 τ

## News

- 2026-09-27 · commit · [Merge pull request #182 from Pareton-ai/bohdan/par-136-builder-cleanu…](https://github.com/Pareton-ai/pareton/commit/36852d8533b4d73e0392ea113bd803c8e8f50b3d) — Pareton-ai/pareton
- 2026-09-24 · commit · [fix(ops): page a full disk while a build holds the cleanup lock](https://github.com/Pareton-ai/pareton/commit/bfc99f86cd976b65b4e790007012d054052c9f23) — Pareton-ai/pareton
- 2026-09-23 · commit · [Merge pull request #184 from Pareton-ai/chore/simplify-artifact-handling](https://github.com/Pareton-ai/pareton/commit/7f60bd35cc078f6ed21ece42e351a685b49047ec) — Pareton-ai/pareton
- 2026-09-23 · commit · [Merge origin/main into artifact handling](https://github.com/Pareton-ai/pareton/commit/ef2a3d5b02066bf13a23c4c0c9638f630c4c8bde) — Pareton-ai/pareton
- 2026-09-23 · commit · [Keep submitted artifacts private](https://github.com/Pareton-ai/pareton/commit/c525d9c832be8356bf70afc98044b1a9fe5826f3) — Pareton-ai/pareton

## Use

```bash
m subnets.sn10/info        # live identity + market (snapshot if bt is down)
m subnets.sn10/news        # scraped news
m subnets.sn10/trades      # 24h alpha tape
m subnets.sn10/daily       # daily candles
python3 orbit/subnets/sn10/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
