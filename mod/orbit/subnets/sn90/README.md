# sn90 — KubeTEE テ

KubeTEE AI Factory: Confidential Computing TEE Multi-Cluster K8s

Bittensor subnet **90** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/KubeTEE-AI/kubetee-subnet) · [url](https://kubetee.ai) · [discord](https://discord.gg/KUeXm9XQG4)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.020545 | -0.00% | -1.49% | +0.03% | 15,245 | 4,809 | 1,449 |

## Last 24h flow

191 trades by 71 coldkeys · 104 buys (702.28 τ) / 87 sells (727.20 τ) · net -24.92 τ

## News

- 2026-10-16 · commit · [docs(subnet): sync east-west/model catalogue + fix stale CPU-GPU refe…](https://github.com/KubeTEE-AI/kubetee-subnet/commit/6cc7b6ee996c528000cb7ed53dd2500fad58c855) — KubeTEE-AI/kubetee-subnet
- 2026-10-16 · commit · [docs(subnet): hardware policy — Blackwell/Vera Rubin + 6th Gen CPUs o…](https://github.com/KubeTEE-AI/kubetee-subnet/commit/e5c4e41f2c7f1f28b1fa8cceddd40e754d133130) — KubeTEE-AI/kubetee-subnet
- 2026-10-16 · commit · [docs(subnet): sync east-west attested mTLS rows with live backends](https://github.com/KubeTEE-AI/kubetee-subnet/commit/375914721d9760e723ed3253ea5047987d4ac613) — KubeTEE-AI/kubetee-subnet
- 2026-10-16 · commit · [docs(subnet): README model lists + validator stats sync](https://github.com/KubeTEE-AI/kubetee-subnet/commit/e988375e768d48b415078af131e74b572e8c6ed9) — KubeTEE-AI/kubetee-subnet
- 2026-10-16 · commit · [docs(subnet): SN28 sayGM GLM-5.2 H200 + GLM-5.3 RadixArk updates](https://github.com/KubeTEE-AI/kubetee-subnet/commit/890f0c413ca8a688dfb8ef116786d0ea1be88365) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(nemotron-omni): 256k context verified + NIM 2.0.13 + gateway mod…](https://github.com/KubeTEE-AI/kubetee-subnet/commit/feeb7495bb93a900a76f63af170bd0404187f871) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(nemotron-omni): sync with NVIDIA build.nvidia.com model card](https://github.com/KubeTEE-AI/kubetee-subnet/commit/d114ca972a815dfde1211e36e0af2c018940e568) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(nemotron-omni): remove the License section from the API doc](https://github.com/KubeTEE-AI/kubetee-subnet/commit/cc502f2a0ce0af867294315f3168b186ed78ee03) — KubeTEE-AI/kubetee-subnet

## Use

```bash
m subnets.sn90/info        # live identity + market (snapshot if bt is down)
m subnets.sn90/news        # scraped news
m subnets.sn90/trades      # 24h alpha tape
m subnets.sn90/daily       # daily candles
python3 orbit/subnets/sn90/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
