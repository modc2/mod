# sn108 — ChipForge モ

ChipForge decentralizes silicon design. Miners anywhere compete to build chips that accelerate AI, scored on real AI models.

Bittensor subnet **108** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/TatsuProject/ChipForge_SN108) · [url](https://www.chipforge.io/) · [discord](https://discord.com/channels/799672011265015819/1408463235082092564)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007451 | -0.01% | -29.19% | -37.17% | 1,407 | 660.1473 | 1,154 |

## Last 24h flow

349 trades by 63 coldkeys · 181 buys (514.67 τ) / 168 sells (634.41 τ) · net -119.74 τ

## News

- 2026-10-14 · commit · [Validator sends its EDA server the batch's time: time_limit_s (deadli…](https://github.com/TatsuProject/ChipForge_SN108/commit/a113ad925e4594d84f7d2fa549e1d968fbe9cfcf) — TatsuProject/ChipForge_SN108
- 2026-10-10 · commit · [On a baseline_epoch change the validator's bar is exactly the server'…](https://github.com/TatsuProject/ChipForge_SN108/commit/4b8de64382a5fe68a159c5bfca5bc45e06fd0df7) — TatsuProject/ChipForge_SN108
- 2026-10-10 · commit · [Validator resets its local challenge best and winner to the server's …](https://github.com/TatsuProject/ChipForge_SN108/commit/4af91b3aa4a8dfc96a4e284a058a984348a0db41) — TatsuProject/ChipForge_SN108
- 2026-10-06 · social · [SN108 Chipforge // Why +300% in 24h](https://www.reddit.com/r/bittensor_/comments/1wz0mjr/sn108_chipforge_why_300_in_24h/) — r/bittensor_
- 2026-10-06 · commit · [Minimum improvement comes from the challenge server (/validator/sync …](https://github.com/TatsuProject/ChipForge_SN108/commit/0f74767f11df47b2c8ca0000d03e1ff91f1ad000) — TatsuProject/ChipForge_SN108
- 2026-10-06 · commit · [.env.example updated](https://github.com/TatsuProject/ChipForge_SN108/commit/3cd9f3cf6f75061f67ae7085d9fea6748a8ed02f) — TatsuProject/ChipForge_SN108
- 2026-10-05 · commit · [docs: validator hardware requirements (min 16 physical cores + 32 GB,…](https://github.com/TatsuProject/ChipForge_SN108/commit/3e019f6fed612b085ca944cb1956d6aa16b015d2) — TatsuProject/ChipForge_SN108
- 2026-10-05 · commit · [Stop tracking CHANGES_V2.md (local change log only) and ignore it](https://github.com/TatsuProject/ChipForge_SN108/commit/9a399e47329879b7be44f135add1c3dfd0f007b0) — TatsuProject/ChipForge_SN108

## Use

```bash
m subnets.sn108/info        # live identity + market (snapshot if bt is down)
m subnets.sn108/news        # scraped news
m subnets.sn108/trades      # 24h alpha tape
m subnets.sn108/daily       # daily candles
python3 orbit/subnets/sn108/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
