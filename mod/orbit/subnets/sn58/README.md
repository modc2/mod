# sn58 — Attune خ

The open frontier for adaptive robot intelligence

Bittensor subnet **58** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/robotensor/attune-subnet) · [url](https://attune.robotensor.ai) · [discord](https://discord.com/channels/799672011265015819/1550516268002578432)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007236 | +0.00% | +0.15% | -15.63% | 6,517 | 1,070 | 465.44 |

## Last 24h flow

127 trades by 53 coldkeys · 67 buys (230.35 τ) / 60 sells (201.47 τ) · net 28.88 τ

## News

- 2026-10-13 · commit · [feat(vector): watch every paid champion, and give a gone king's crown…](https://github.com/robotensor/attune-subnet/commit/352b96a71708f6b4bda9851789afe7e6b8397732) — robotensor/attune-subnet
- 2026-10-11 · commit · [fix(vector): read the lane's state only after the mirror's take-up](https://github.com/robotensor/attune-subnet/commit/5a55cf9c5fa29e17a112816a9b07649861b063bd) — robotensor/attune-subnet
- 2026-10-11 · commit · [feat(vector): a validator on another host takes the lane up from the …](https://github.com/robotensor/attune-subnet/commit/7e106274249f739eabc7699be0ddef6e9f7ffaa0) — robotensor/attune-subnet
- 2026-10-09 · commit · [feat(vector): ignore commitments made before the start block](https://github.com/robotensor/attune-subnet/commit/25fff1ba5346ba05f98bfa4d16bc543042a23af9) — robotensor/attune-subnet
- 2026-10-08 · commit · [chore: keep config/ out of the repository; each host keeps its own](https://github.com/robotensor/attune-subnet/commit/ba4fba46c3b183edfc4bd1c4ef2098e454447e7d) — robotensor/attune-subnet
- 2026-10-08 · commit · [fix(miner): `attune miner status` checks the Hub with the submission …](https://github.com/robotensor/attune-subnet/commit/ab0a7a0310c7fd6aa1b104a4ea64115823ed2084) — robotensor/attune-subnet
- 2026-10-07 · commit · [refactor: no `attune doctor`](https://github.com/robotensor/attune-subnet/commit/0f16095014543bcce61d7030b4fae5d9666e9e64) — robotensor/attune-subnet
- 2026-10-07 · commit · [feat(miner): robotensor-attune, the miner's package](https://github.com/robotensor/attune-subnet/commit/09440b29962630d436033c6b76935058bd16513e) — robotensor/attune-subnet

## Use

```bash
m subnets.sn58/info        # live identity + market (snapshot if bt is down)
m subnets.sn58/news        # scraped news
m subnets.sn58/trades      # 24h alpha tape
m subnets.sn58/daily       # daily candles
python3 orbit/subnets/sn58/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
