# sn31 — rec4ll ז

Recall provides decentralized retrieval-augmented generation to Bittensor. Miners serve embedding models, vector search, and LLM inference. Validators independently evaluate retrieval accuracy and answer quality. The subnet discovers the best RAG pipeline through open competition and routes user queries to the top performers. Think of it as an always-improving, community-owned search engine with citations.

Bittensor subnet **31** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004078 | -0.00% | -0.19% | +1.55% | 9,648 | 1,821 | 950.88 |

## Last 24h flow

46 trades by 26 coldkeys · 25 buys (474.67 τ) / 21 sells (466.74 τ) · net 7.93 τ

## Use

```bash
m subnets.sn31/info        # live identity + market (snapshot if bt is down)
m subnets.sn31/news        # scraped news
m subnets.sn31/trades      # 24h alpha tape
m subnets.sn31/daily       # daily candles
python3 orbit/subnets/sn31/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
