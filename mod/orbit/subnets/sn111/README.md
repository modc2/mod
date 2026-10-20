# sn111 — Claims Ё

Turning scientific literature into a structured claim-evidence graph

Bittensor subnet **111** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/DeSciClaims/Claims) · [discord](https://discord.com/channels/799672011265015819/1515007366016401599)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004055 | -0.00% | +0.03% | -6.23% | 20,403 | 5,444 | 9.49 |

## Last 24h flow

6 trades by 6 coldkeys · 2 buys (5.16 τ) / 4 sells (3.03 τ) · net 2.13 τ

## News

- 2026-10-19 · release · [v1.1.0](https://github.com/DeSciClaims/Claims/releases/tag/v1.1.0) — DeSciClaims/Claims
- 2026-10-19 · commit · [Enable equivalence-group shared coverage by default](https://github.com/DeSciClaims/Claims/commit/4fcb7307d67e62a8962c9c7d9582e390b18c4a48) — DeSciClaims/Claims
- 2026-10-19 · commit · [Integrate approved-group fractional coverage into validator scoring](https://github.com/DeSciClaims/Claims/commit/0e0235a9cd9eb3ef6098227cb57ac1343b635f3e) — DeSciClaims/Claims
- 2026-10-19 · commit · [Replay group-only fractional coverage with explicit mixed-claim policies](https://github.com/DeSciClaims/Claims/commit/edc406cc528d33029c9ada21985a8e824a1b5ec7) — DeSciClaims/Claims
- 2026-10-19 · commit · [Compare historical mainnet usage and replay decision changes](https://github.com/DeSciClaims/Claims/commit/57662de4c71320738f1c85e1d0bd6c5d3626f122) — DeSciClaims/Claims
- 2026-10-19 · commit · [Add isolated full mainnet batch replay with historical inputs](https://github.com/DeSciClaims/Claims/commit/d6f63b5d86258930b3a610740313530705a638af) — DeSciClaims/Claims
- 2026-10-15 · commit · [fix(consensus): log finalization status for voided rounds](https://github.com/DeSciClaims/Claims/commit/41573a43589f96ce27746e24426c3f025134ecae) — DeSciClaims/Claims
- 2026-10-13 · commit · [Docs: update agent_v1 vs section_context_v1 schema](https://github.com/DeSciClaims/Claims/commit/84d9f5f1ac7ca480c90ff529ae114d2f3902b02b) — DeSciClaims/Claims

## Use

```bash
m subnets.sn111/info        # live identity + market (snapshot if bt is down)
m subnets.sn111/news        # scraped news
m subnets.sn111/trades      # 24h alpha tape
m subnets.sn111/daily       # daily candles
python3 orbit/subnets/sn111/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
