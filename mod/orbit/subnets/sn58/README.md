# sn58 — Attune خ

The open frontier for adaptive robot intelligence

Bittensor subnet **58** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/robotensor/attune-subnet) · [url](https://attune.robotensor.ai) · [discord](https://discord.com/channels/799672011265015819/1550516268002578432)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008576 | +0.31% | -26.96% | +35.34% | 7,321 | 1,164 | 1,572 |

## Last 24h flow

317 trades by 96 coldkeys · 140 buys (686.50 τ) / 177 sells (878.81 τ) · net -192.32 τ

## News

- 2026-10-09 · commit · [feat(vector): ignore commitments made before the start block](https://github.com/robotensor/attune-subnet/commit/25fff1ba5346ba05f98bfa4d16bc543042a23af9) — robotensor/attune-subnet
- 2026-10-08 · commit · [chore: keep config/ out of the repository; each host keeps its own](https://github.com/robotensor/attune-subnet/commit/ba4fba46c3b183edfc4bd1c4ef2098e454447e7d) — robotensor/attune-subnet
- 2026-10-08 · commit · [fix(miner): `attune miner status` checks the Hub with the submission …](https://github.com/robotensor/attune-subnet/commit/ab0a7a0310c7fd6aa1b104a4ea64115823ed2084) — robotensor/attune-subnet
- 2026-10-07 · commit · [refactor: no `attune doctor`](https://github.com/robotensor/attune-subnet/commit/0f16095014543bcce61d7030b4fae5d9666e9e64) — robotensor/attune-subnet
- 2026-10-07 · commit · [feat(miner): robotensor-attune, the miner's package](https://github.com/robotensor/attune-subnet/commit/09440b29962630d436033c6b76935058bd16513e) — robotensor/attune-subnet
- 2026-10-07 · commit · [docs: the Attune banner as the header image](https://github.com/robotensor/attune-subnet/commit/3e9d463e2ae9b59a73f6713febd1e8b4b37bac71) — robotensor/attune-subnet
- 2026-10-07 · commit · [feat: the subnet is Attune; the command is `attune`](https://github.com/robotensor/attune-subnet/commit/7f708824e4b2ae1f518158a6b2f67e51d1fff162) — robotensor/attune-subnet
- 2026-10-05 · commit · [docs: new header image](https://github.com/robotensor/attune-subnet/commit/8582440d390ae56a7d12ba42e84a918d5bd86a32) — robotensor/attune-subnet

## Use

```bash
m subnets.sn58/info        # live identity + market (snapshot if bt is down)
m subnets.sn58/news        # scraped news
m subnets.sn58/trades      # 24h alpha tape
m subnets.sn58/daily       # daily candles
python3 orbit/subnets/sn58/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
