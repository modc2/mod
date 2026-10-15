# sn51 — lium.io ת

Agent-first GPU cloud. Rent NVIDIA GPUs via web, CLI, SDK, or API. Providers earn 95% rental revenue + bonuses.

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · [discord](https://discord.gg/lium)

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.109084 | +0.00% | +0.93% | +8.89% | 669,662 | 178,227 | 5,755 |

## Last 24h flow

711 trades by 267 coldkeys · 391 buys (2,896 τ) / 320 sells (2,452 τ) · net 444.85 τ

## News

- 2026-10-14 · commit · [NO-TICKET - validator: the spot pay flag is named for the backend's p…](https://github.com/Datura-ai/lium-io/commit/661d8c2040283d51552d6e14c7b86eaecaf1b3c2) — Datura-ai/lium-io
- 2026-10-14 · commit · [Revert "DAH-3938 - \[P1\] validator: filler power-limit exemption keyed…](https://github.com/Datura-ai/lium-io/commit/c9ca156cfe792d434297c4abd43c095f2221c977) — Datura-ai/lium-io
- 2026-10-14 · commit · [DAH-4094 - \[P2\] Say a CLI price update was saved, not that the listin…](https://github.com/Datura-ai/lium-io/commit/98a331d1b6118ad421a7b4eb0b3caf061337e8cf) — Datura-ai/lium-io
- 2026-10-14 · commit · [DAH-4121 - \[P2\] validator: an untrusted SSH host key says the port re…](https://github.com/Datura-ai/lium-io/commit/e21f288e82e24890610905bc18b9969a2959be13) — Datura-ai/lium-io
- 2026-10-14 · commit · [DAH-3798 - \[P1\] rental containers: swap off and a renter-first OOM sc…](https://github.com/Datura-ai/lium-io/commit/d73ce8b1f2f283b9084a0855ac2f30f37fbc20d1) — Datura-ai/lium-io
- 2026-10-14 · commit · [DAH-4035 - \[P1\] Validator: resolve hostname nodes in their own thread…](https://github.com/Datura-ai/lium-io/commit/58ce4fa37fe42022ad3b2dae01f0ce6542c5f45a) — Datura-ai/lium-io
- 2026-10-14 · commit · [DAH-4272 - \[P1\] Record which hotkey owns a node's verified record (#1…](https://github.com/Datura-ai/lium-io/commit/ba4085b02e6907da9b3ad37331bfd42568d9d691) — Datura-ai/lium-io
- 2026-10-14 · commit · [DAH-4315 - \[P1\] Count a new rental pod with no container name yet as …](https://github.com/Datura-ai/lium-io/commit/0476283bfaa9a581074b0237cfbf3aa2873ec829) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
