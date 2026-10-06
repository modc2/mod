# sn93 — Bitcast ᚃ

The Decentralized Creators Economy

Bittensor subnet **93** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/bitcast-network/bitcast) · [url](https://stats.bitcast.network/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.020791 | -0.23% | -0.90% | +13.60% | 109,360 | 25,273 | 1,333 |

## Last 24h flow

211 trades by 127 coldkeys · 76 buys (540.73 τ) / 135 sells (707.32 τ) · net -166.60 τ

## News

- 2026-09-17 · commit · [docs: replace CLAUDE.md with AGENTS.md + on-chain liveness verificati…](https://github.com/bitcast-network/bitcast/commit/e27397ed0a10402d0a14e6ae8a327802fcee8c43) — bitcast-network/bitcast
- 2026-09-17 · commit · [chore: remove subnet identity logo from repo (#175)](https://github.com/bitcast-network/bitcast/commit/b3e6f81761e0de1edac55dead5d8f83a50c40021) — bitcast-network/bitcast
- 2026-09-06 · commit · [fix: deploy from the terraform-managed TD revision (no template drift…](https://github.com/bitcast-network/bitcast/commit/1a3210895c3fe44daec25f544a2c7b27de3c5dfe) — bitcast-network/bitcast
- 2026-09-06 · commit · [feat: host subnet identity logo in-repo for chain identity URL (#170)](https://github.com/bitcast-network/bitcast/commit/d629331b2ee8d813e23e28d09f1a25ee96152236) — bitcast-network/bitcast
- 2026-09-06 · commit · [chore: scope validator deploy to code/config paths (#171)](https://github.com/bitcast-network/bitcast/commit/8e8a0235d27aff4f9383389a7795d9e78ed7a0da) — bitcast-network/bitcast

## Use

```bash
m subnets.sn93/info        # live identity + market (snapshot if bt is down)
m subnets.sn93/news        # scraped news
m subnets.sn93/trades      # 24h alpha tape
m subnets.sn93/daily       # daily candles
python3 orbit/subnets/sn93/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
