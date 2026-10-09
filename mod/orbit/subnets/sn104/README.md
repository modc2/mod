# sn104 — TAOstatus Բ

Predictive Intelligence Layer for Bittensor

Bittensor subnet **104** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/taostatus/taostatus-subnet) · [url](https://taostatus.com/) · [discord](https://discord.gg/t8k9vm4KMG)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004119 | -0.00% | -0.03% | -0.92% | 19,356 | 5,893 | 0.94 |

## Last 24h flow

4 trades by 4 coldkeys · 0 buys (0.00 τ) / 4 sells (0.70 τ) · net -0.70 τ

## News

- 2026-10-01 · commit · [Merge pull request #17 from taostatus/feat/backend-integration](https://github.com/taostatus/taostatus-subnet/commit/330242d5aa061c145167797fd03e7d5a7d0a84e6) — taostatus/taostatus-subnet
- 2026-10-01 · commit · [feat: agent details to migrate on marketplace](https://github.com/taostatus/taostatus-subnet/commit/c97ce8239b4a04c6d60c1c9d23f42425d4f96ecc) — taostatus/taostatus-subnet
- 2026-09-30 · commit · [Merge pull request #16 from taostatus/feat/mech](https://github.com/taostatus/taostatus-subnet/commit/b2bd771c4f437dacca199aa3c8876f442c0cd155) — taostatus/taostatus-subnet
- 2026-09-30 · commit · [feat: split subnet into two mechanisms (mech 0 LLM-key, mech 1 securi…](https://github.com/taostatus/taostatus-subnet/commit/4a740ff484fff676401a5538d5ae4f8a9be6555c) — taostatus/taostatus-subnet
- 2026-09-30 · commit · [Merge pull request #15 from taostatus/feat/security-validator](https://github.com/taostatus/taostatus-subnet/commit/38b4d38e8f492a8b22afd0f349b7330645e22a4d) — taostatus/taostatus-subnet

## Use

```bash
m subnets.sn104/info        # live identity + market (snapshot if bt is down)
m subnets.sn104/news        # scraped news
m subnets.sn104/trades      # 24h alpha tape
m subnets.sn104/daily       # daily candles
python3 orbit/subnets/sn104/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
