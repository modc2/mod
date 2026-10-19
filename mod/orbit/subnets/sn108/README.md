# sn108 — ChipForge モ

ChipForge decentralizes silicon design. Miners anywhere compete to build chips that accelerate AI, scored on real AI models.

Bittensor subnet **108** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/TatsuProject/ChipForge_SN108) · [url](https://www.chipforge.io/) · [discord](https://discord.com/channels/799672011265015819/1408463235082092564)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007443 | — | +6.18% | -43.52% | 1,560 | 659.8051 | 883.95 |

## Last 24h flow

360 trades by 58 coldkeys · 207 buys (451.79 τ) / 153 sells (428.89 τ) · net 22.90 τ

## News

- 2026-10-17 · commit · [Merge pull request #5 from TatsuProject/feature/record-ladder](https://github.com/TatsuProject/ChipForge_SN108/commit/d68c22f299a9024ca4fd6028fea225eb31c5402e) — TatsuProject/ChipForge_SN108
- 2026-10-17 · commit · [Ladder follows the server's repeat_hotkeys (missing = off): a hotkey …](https://github.com/TatsuProject/ChipForge_SN108/commit/903d7c73410c558c375bc6406add50dcae3b463c) — TatsuProject/ChipForge_SN108
- 2026-10-17 · commit · [Merge pull request #4 from TatsuProject/feature/record-ladder](https://github.com/TatsuProject/ChipForge_SN108/commit/2ca53a203004978e3da85451287179640968668b) — TatsuProject/ChipForge_SN108
- 2026-10-17 · commit · [Ladder report while emissions are banned: the header says "emissions …](https://github.com/TatsuProject/ChipForge_SN108/commit/4c8f9f6e77528b61097743db391850a4a2c66a4a) — TatsuProject/ChipForge_SN108
- 2026-10-16 · commit · [Validators don't pay records the server marks banned (coldkey banned …](https://github.com/TatsuProject/ChipForge_SN108/commit/71124d9a22141e1fb7a6f8e65ce0ab44dd702f27) — TatsuProject/ChipForge_SN108
- 2026-10-14 · commit · [Validator sends its EDA server the batch's time: time_limit_s (deadli…](https://github.com/TatsuProject/ChipForge_SN108/commit/a113ad925e4594d84f7d2fa549e1d968fbe9cfcf) — TatsuProject/ChipForge_SN108
- 2026-10-10 · commit · [On a baseline_epoch change the validator's bar is exactly the server'…](https://github.com/TatsuProject/ChipForge_SN108/commit/4b8de64382a5fe68a159c5bfca5bc45e06fd0df7) — TatsuProject/ChipForge_SN108
- 2026-10-10 · commit · [Validator resets its local challenge best and winner to the server's …](https://github.com/TatsuProject/ChipForge_SN108/commit/4af91b3aa4a8dfc96a4e284a058a984348a0db41) — TatsuProject/ChipForge_SN108

## Use

```bash
m subnets.sn108/info        # live identity + market (snapshot if bt is down)
m subnets.sn108/news        # scraped news
m subnets.sn108/trades      # 24h alpha tape
m subnets.sn108/daily       # daily candles
python3 orbit/subnets/sn108/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
