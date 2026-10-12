# sn31 — rec4ll ז

Recall provides decentralized retrieval-augmented generation to Bittensor. Miners serve embedding models, vector search, and LLM inference. Validators independently evaluate retrieval accuracy and answer quality. The subnet discovers the best RAG pipeline through open competition and routes user queries to the top performers. Think of it as an always-improving, community-owned search engine with citations.

Bittensor subnet **31** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004010 | +2.16% | +1.38% | +0.54% | 9,765 | 1,805 | 47.67 |

## Last 24h flow

17 trades by 13 coldkeys · 4 buys (30.01 τ) / 13 sells (16.94 τ) · net 13.07 τ

## Use

```bash
m subnets.sn31/info        # live identity + market (snapshot if bt is down)
m subnets.sn31/news        # scraped news
m subnets.sn31/trades      # 24h alpha tape
m subnets.sn31/daily       # daily candles
python3 orbit/subnets/sn31/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
