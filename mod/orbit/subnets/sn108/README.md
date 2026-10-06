# sn108 — ChipForge モ

ChipForge decentralizes silicon design. Miners anywhere compete to build chips that accelerate AI, scored on real AI models.

Bittensor subnet **108** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/TatsuProject/ChipForge_SN108) · [url](https://www.chipforge.io/) · [discord](https://discord.com/channels/799672011265015819/1408463235082092564)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.015223 | -3.13% | +240.03% | +242.71% | 1,785 | 943.6231 | 8,205 |

## Last 24h flow

1149 trades by 157 coldkeys · 748 buys (4,324 τ) / 401 sells (3,884 τ) · net 439.40 τ

## News

- 2026-10-05 · commit · [docs: validator hardware requirements (min 16 physical cores + 32 GB,…](https://github.com/TatsuProject/ChipForge_SN108/commit/3e019f6fed612b085ca944cb1956d6aa16b015d2) — TatsuProject/ChipForge_SN108
- 2026-10-05 · commit · [Stop tracking CHANGES_V2.md (local change log only) and ignore it](https://github.com/TatsuProject/ChipForge_SN108/commit/9a399e47329879b7be44f135add1c3dfd0f007b0) — TatsuProject/ChipForge_SN108
- 2026-10-01 · commit · [Validator logs explain the MIN_IMPROVEMENT_PERCENT decision: % gain o…](https://github.com/TatsuProject/ChipForge_SN108/commit/c834a38011fa514a7101b5b52a4659373552a891) — TatsuProject/ChipForge_SN108
- 2026-10-01 · commit · [Docs and config for public use: registration/permit/secret-key/EDA pr…](https://github.com/TatsuProject/ChipForge_SN108/commit/684c833fc87023598a982956adbf2373270b39e8) — TatsuProject/ChipForge_SN108
- 2026-09-30 · commit · [Merge pull request #3 from TatsuProject/improvements/v2](https://github.com/TatsuProject/ChipForge_SN108/commit/306a82af6d03ea15ccaa8d6222ed19c7f0383af5) — TatsuProject/ChipForge_SN108
- 2026-09-30 · commit · [Validator: periodic weight status line, change-only state logs, 409 a…](https://github.com/TatsuProject/ChipForge_SN108/commit/94eac8441c783316c97883162d7c00614b724842) — TatsuProject/ChipForge_SN108
- 2026-09-30 · commit · [Miner downloads challenge packages from its own CHALLENGE_API_URL, no…](https://github.com/TatsuProject/ChipForge_SN108/commit/f3d1e657f8b2b193f230352138cd58579269ade0) — TatsuProject/ChipForge_SN108

## Use

```bash
m subnets.sn108/info        # live identity + market (snapshot if bt is down)
m subnets.sn108/news        # scraped news
m subnets.sn108/trades      # 24h alpha tape
m subnets.sn108/daily       # daily candles
python3 orbit/subnets/sn108/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
