# sn31 — rec4ll ז

Recall provides decentralized retrieval-augmented generation to Bittensor. Miners serve embedding models, vector search, and LLM inference. Validators independently evaluate retrieval accuracy and answer quality. The subnet discovers the best RAG pipeline through open competition and routes user queries to the top performers. Think of it as an always-improving, community-owned search engine with citations.

Bittensor subnet **31** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003884 | -0.00% | +2.06% | -6.45% | 9,402 | 1,777 | 43.87 |

## Last 24h flow

12 trades by 9 coldkeys · 6 buys (30.94 τ) / 6 sells (12.24 τ) · net 18.70 τ

## Use

```bash
m subnets.sn31/info        # live identity + market (snapshot if bt is down)
m subnets.sn31/news        # scraped news
m subnets.sn31/trades      # 24h alpha tape
m subnets.sn31/daily       # daily candles
python3 orbit/subnets/sn31/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
