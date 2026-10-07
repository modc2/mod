# sn31 — rec4ll ז

Recall provides decentralized retrieval-augmented generation to Bittensor. Miners serve embedding models, vector search, and LLM inference. Validators independently evaluate retrieval accuracy and answer quality. The subnet discovers the best RAG pipeline through open competition and routes user queries to the top performers. Think of it as an always-improving, community-owned search engine with citations.

Bittensor subnet **31** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003838 | -0.00% | -3.37% | -4.34% | 9,209 | 1,766 | 37.30 |

## Last 24h flow

20 trades by 14 coldkeys · 5 buys (3.38 τ) / 15 sells (32.39 τ) · net -29.01 τ

## Use

```bash
m subnets.sn31/info        # live identity + market (snapshot if bt is down)
m subnets.sn31/news        # scraped news
m subnets.sn31/trades      # 24h alpha tape
m subnets.sn31/daily       # daily candles
python3 orbit/subnets/sn31/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
