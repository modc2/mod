# sn31 — rec4ll ז

Recall provides decentralized retrieval-augmented generation to Bittensor. Miners serve embedding models, vector search, and LLM inference. Validators independently evaluate retrieval accuracy and answer quality. The subnet discovers the best RAG pipeline through open competition and routes user queries to the top performers. Think of it as an always-improving, community-owned search engine with citations.

Bittensor subnet **31** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003902 | -0.00% | -0.53% | -1.35% | 9,670 | 1,781 | 96.73 |

## Last 24h flow

17 trades by 10 coldkeys · 4 buys (45.99 τ) / 13 sells (49.73 τ) · net -3.74 τ

## Use

```bash
m subnets.sn31/info        # live identity + market (snapshot if bt is down)
m subnets.sn31/news        # scraped news
m subnets.sn31/trades      # 24h alpha tape
m subnets.sn31/daily       # daily candles
python3 orbit/subnets/sn31/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
