# sn104 — TAOstatus Բ

Predictive Intelligence Layer for Bittensor

Bittensor subnet **104** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/taostatus/taostatus-subnet) · [url](https://taostatus.com/) · [discord](https://discord.gg/t8k9vm4KMG)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004120 | +0.02% | +0.01% | -0.57% | 19,376 | 5,893 | 30.72 |

## Last 24h flow

5 trades by 3 coldkeys · 2 buys (15.00 τ) / 3 sells (15.00 τ) · net 0.00 τ

## News

- 2026-10-09 · commit · [Merge pull request #20 from taostatus/main](https://github.com/taostatus/taostatus-subnet/commit/529f7137825714c088ee18a4f64199b518886641) — taostatus/taostatus-subnet
- 2026-10-09 · commit · [Merge pull request #19 from taostatus/fix/burn-100-mainnet](https://github.com/taostatus/taostatus-subnet/commit/31e6d55876b54aa8b560b0b610a1f03d25684b51) — taostatus/taostatus-subnet
- 2026-10-09 · commit · [chore(weights): burn 100% of emission on mainnet while bootstrapping](https://github.com/taostatus/taostatus-subnet/commit/2b0561a10d16e2a1a37a5c6d1c199b269b57c265) — taostatus/taostatus-subnet
- 2026-10-02 · commit · [Merge pull request #18 from taostatus/dev](https://github.com/taostatus/taostatus-subnet/commit/45241ec2b737226340f8ee2dce17b12373bed3a9) — taostatus/taostatus-subnet
- 2026-10-01 · commit · [Merge pull request #17 from taostatus/feat/backend-integration](https://github.com/taostatus/taostatus-subnet/commit/330242d5aa061c145167797fd03e7d5a7d0a84e6) — taostatus/taostatus-subnet
- 2026-10-01 · commit · [feat: agent details to migrate on marketplace](https://github.com/taostatus/taostatus-subnet/commit/c97ce8239b4a04c6d60c1c9d23f42425d4f96ecc) — taostatus/taostatus-subnet
- 2026-09-30 · commit · [Merge pull request #16 from taostatus/feat/mech](https://github.com/taostatus/taostatus-subnet/commit/b2bd771c4f437dacca199aa3c8876f442c0cd155) — taostatus/taostatus-subnet
- 2026-09-30 · commit · [feat: split subnet into two mechanisms (mech 0 LLM-key, mech 1 securi…](https://github.com/taostatus/taostatus-subnet/commit/4a740ff484fff676401a5538d5ae4f8a9be6555c) — taostatus/taostatus-subnet

## Use

```bash
m subnets.sn104/info        # live identity + market (snapshot if bt is down)
m subnets.sn104/news        # scraped news
m subnets.sn104/trades      # 24h alpha tape
m subnets.sn104/daily       # daily candles
python3 orbit/subnets/sn104/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
