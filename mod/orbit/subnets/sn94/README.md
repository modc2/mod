# sn94 — Cathedral ᚄ

CPU sandboxes powering reinforcement learning on Bittensor.

Bittensor subnet **94** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/cathedralai/cathedral-sandbox) · [url](https://cathedral.computer/) · [discord](https://discord.com/channels/799672011265015819/1526241812589711571)

Fleet mods for this subnet: `cathedral`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005098 | -0.94% | -5.79% | +21.81% | 12,946 | 2,443 | 1,148 |

## Last 24h flow

188 trades by 75 coldkeys · 83 buys (537.13 τ) / 105 sells (613.56 τ) · net -76.42 τ

## News

- 2026-10-01 · commit · [snp friend probe: an unavailable AMD verifier is inconclusive, never …](https://github.com/cathedralai/cathedral-sandbox/commit/244f9f52eaf4b570886e6d6eab734cecd0b39d78) — cathedralai/cathedral-sandbox
- 2026-10-01 · commit · [Merge pull request #256 from cathedralai/fix/snp-umask-and-kds-thrott…](https://github.com/cathedralai/cathedral-sandbox/commit/de8a061e1641b8e65fc4b1c5f27ff0ae4066e502) — cathedralai/cathedral-sandbox
- 2026-10-01 · commit · [snp: keep cached certificates when only verify attestation fails](https://github.com/cathedralai/cathedral-sandbox/commit/6b214b4a7a41e37090404d51c6fb585c51dca8cc) — cathedralai/cathedral-sandbox
- 2026-10-01 · commit · [snp: cache AMD certificates per chip and TCB, back off on KDS throttling](https://github.com/cathedralai/cathedral-sandbox/commit/a57855579cd4b06182c3fdc9a519fd3884fba1b7) — cathedralai/cathedral-sandbox
- 2026-10-01 · commit · [fix(snp): fetch AMD certificates with an owner-only umask](https://github.com/cathedralai/cathedral-sandbox/commit/c85541517e77520d5befd245e74f75453a7ae2a1) — cathedralai/cathedral-sandbox

## Use

```bash
m subnets.sn94/info        # live identity + market (snapshot if bt is down)
m subnets.sn94/news        # scraped news
m subnets.sn94/trades      # 24h alpha tape
m subnets.sn94/daily       # daily candles
python3 orbit/subnets/sn94/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
