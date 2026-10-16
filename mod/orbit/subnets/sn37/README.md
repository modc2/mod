# sn37 — Aurelius ל

Decentralized Alignment of Artificial Intelligence

Bittensor subnet **37** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/Aurelius-Protocol/Aurelius-Protocol) · [url](https://aureliusaligned.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002800 | -0.00% | -1.25% | +2.26% | 17,520 | 7,299 | 141.68 |

## Last 24h flow

30 trades by 16 coldkeys · 19 buys (47.78 τ) / 11 sells (89.45 τ) · net -41.67 τ

## News

- 2026-10-16 · commit · [fix(miner): support reviewed mainnet runtime479 (#30)](https://github.com/Aurelius-Protocol/Aurelius-Protocol/commit/c664c09b68f749b166878c79eb4e3346702666f2) — Aurelius-Protocol/Aurelius-Protocol
- 2026-10-14 · commit · [docs: describe planned stake-retention mechanism in INCENTIVES.md](https://github.com/Aurelius-Protocol/Aurelius-Protocol/commit/294b3a49f301ed4b3249405a59d291fbe745655c) — Aurelius-Protocol/Aurelius-Protocol
- 2026-10-14 · commit · [Simplify miner docs and require incentive-terms agreement (#29)](https://github.com/Aurelius-Protocol/Aurelius-Protocol/commit/584685675065983536c9ff9c2016f17efc89ac3b) — Aurelius-Protocol/Aurelius-Protocol
- 2026-10-13 · commit · [Delete docs/evidence directory](https://github.com/Aurelius-Protocol/Aurelius-Protocol/commit/3e38c9f7141ecc90523ee9925316a3a14bab55c7) — Aurelius-Protocol/Aurelius-Protocol
- 2026-10-13 · commit · [Replace the legacy public tree with the v5 miner and participant guid…](https://github.com/Aurelius-Protocol/Aurelius-Protocol/commit/31b119e481696276435eefd12d6e5d41fbd9157f) — Aurelius-Protocol/Aurelius-Protocol
- 2026-10-06 · commit · [feat(validator)!: default-off master switch VALIDATOR_ENABLED (#26)](https://github.com/Aurelius-Protocol/Aurelius-Protocol/commit/99ef804cc9cd4a627f2edd112100202ad7f989fa) — Aurelius-Protocol/Aurelius-Protocol

## Use

```bash
m subnets.sn37/info        # live identity + market (snapshot if bt is down)
m subnets.sn37/news        # scraped news
m subnets.sn37/trades      # 24h alpha tape
m subnets.sn37/daily       # daily candles
python3 orbit/subnets/sn37/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
