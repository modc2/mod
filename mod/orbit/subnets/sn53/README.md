# sn53 — engy ب

Verified inference for frontier open models.

Bittensor subnet **53** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/hanlinai/engy) · [url](https://engy.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.038353 | — | -2.61% | -2.45% | 240,177 | 40,862 | 6,923 |

## Last 24h flow

392 trades by 165 coldkeys · 199 buys (3,046 τ) / 193 sells (3,724 τ) · net -677.99 τ

## News

- 2026-10-18 · commit · [Merge pull request #55 from hanlinai/feat/multi-entrance-legs](https://github.com/hanlinai/engy/commit/70a4f104d7fc02c0f29e9bdbbf0146c70921e353) — hanlinai/engy
- 2026-10-18 · commit · [tee_miner: default to half the legs on the LB, half on Cloudflare, sh…](https://github.com/hanlinai/engy/commit/1829b9874e1b90f000570b05654578ae94d5f895) — hanlinai/engy
- 2026-10-17 · commit · [tee_miner: Cloudflare is a backup door, not a second home](https://github.com/hanlinai/engy/commit/01ff05a8bb46d0d4dec44d29604bf067df3a92eb) — hanlinai/engy
- 2026-10-17 · commit · [tee_miner: spread the legs across several gateway doors](https://github.com/hanlinai/engy/commit/a4c161396491c40008dd0160a5aa8c1bae9abd65) — hanlinai/engy
- 2026-10-13 · commit · [Merge pull request #53 from hanlinai/fix/tee-miner-no-body-in-logs](https://github.com/hanlinai/engy/commit/29ad734b006442bbedd28d06ed06ff8dbe42a0aa) — hanlinai/engy
- 2026-10-13 · commit · [tee_miner: never write the request to the miner log](https://github.com/hanlinai/engy/commit/fe55344aba142cf9d8496df3cf3549bb80028715) — hanlinai/engy
- 2026-10-12 · commit · [Merge pull request #52 from hanlinai/fix/tee-miner-kv-heartbeat-10s](https://github.com/hanlinai/engy/commit/8ee9d6a5ddb293e7fa21e3f0da845fa335e88eac) — hanlinai/engy
- 2026-10-12 · commit · [tee_miner: send the KV-carrying heartbeat every 10 s, not 30 s](https://github.com/hanlinai/engy/commit/811ca2c2f60f1854c0eb0a1945ba7eae3f66dc96) — hanlinai/engy

## Use

```bash
m subnets.sn53/info        # live identity + market (snapshot if bt is down)
m subnets.sn53/news        # scraped news
m subnets.sn53/trades      # 24h alpha tape
m subnets.sn53/daily       # daily candles
python3 orbit/subnets/sn53/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
