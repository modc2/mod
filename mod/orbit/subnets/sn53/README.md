# sn53 — engy ب

Verified inference for frontier open models.

Bittensor subnet **53** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/hanlinai/engy) · [url](https://engy.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.039350 | +0.20% | +1.27% | +3.65% | 246,129 | 41,355 | 5,766 |

## Last 24h flow

421 trades by 217 coldkeys · 195 buys (2,872 τ) / 226 sells (2,726 τ) · net 146.78 τ

## News

- 2026-10-13 · commit · [Merge pull request #53 from hanlinai/fix/tee-miner-no-body-in-logs](https://github.com/hanlinai/engy/commit/29ad734b006442bbedd28d06ed06ff8dbe42a0aa) — hanlinai/engy
- 2026-10-13 · commit · [tee_miner: never write the request to the miner log](https://github.com/hanlinai/engy/commit/fe55344aba142cf9d8496df3cf3549bb80028715) — hanlinai/engy
- 2026-10-12 · commit · [Merge pull request #52 from hanlinai/fix/tee-miner-kv-heartbeat-10s](https://github.com/hanlinai/engy/commit/8ee9d6a5ddb293e7fa21e3f0da845fa335e88eac) — hanlinai/engy
- 2026-10-12 · commit · [tee_miner: send the KV-carrying heartbeat every 10 s, not 30 s](https://github.com/hanlinai/engy/commit/811ca2c2f60f1854c0eb0a1945ba7eae3f66dc96) — hanlinai/engy
- 2026-09-29 · commit · [Merge pull request #51 from hanlinai/docs/miner-provider-docs-pointer](https://github.com/hanlinai/engy/commit/647c767e5994c1b041a014c51e76b0cfbc3cc936) — hanlinai/engy
- 2026-09-29 · commit · [docs: point MINER.md at the provider onboarding guide](https://github.com/hanlinai/engy/commit/5087bf45262373f3f21ddc7aa7181ba0a6e37d4d) — hanlinai/engy
- 2026-09-29 · commit · [Merge pull request #50 from hanlinai/docs/miner-lb-endpoint](https://github.com/hanlinai/engy/commit/d96784d7aa8c336f624b5938aad5ac64b0bcd0db) — hanlinai/engy
- 2026-09-29 · commit · [docs: recommend wss://lb.engy.ai/gw as the miner endpoint](https://github.com/hanlinai/engy/commit/ba18a8aa3b4f0eaa3d0dc374f5f12b4f4a1833ef) — hanlinai/engy

## Use

```bash
m subnets.sn53/info        # live identity + market (snapshot if bt is down)
m subnets.sn53/news        # scraped news
m subnets.sn53/trades      # 24h alpha tape
m subnets.sn53/daily       # daily candles
python3 orbit/subnets/sn53/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
