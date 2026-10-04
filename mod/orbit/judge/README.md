# judge

Approval as a **multisig of agents**. A panel is a bench of judges; an input
goes before the panel, every judge scores it **0–100**, and the weighted
average must reach the panel's **threshold** or the verdict is **FAIL**.
The **creator** of the panel sets all the params and is the only one who can
change them.

Fail-closed by design: a judge that errors doesn't vote, and if fewer than
`min_votes` judges manage to vote the verdict fails regardless of the average
— silence never counts as approval.

## The params (set by the creator)

| param | meaning | default |
|---|---|---|
| `judges` | the bench — a list of judge specs (below) | required |
| `threshold` | average score (0–100) needed to approve | `60` |
| `min_votes` | quorum — judges that must vote for a verdict to count | all of them |
| `creator` | who owns the panel; the only identity that can update/remove it | required |

Two judge kinds:

```jsonc
// an agent: scores against a criteria prompt via any OpenAI-compatible endpoint
{"name": "clarity", "kind": "llm", "prompt": "Is the text clear and specific?",
 "model": "auto",                       // optional
 "url": "http://localhost:50600/v1/chat/completions",  // optional; this is the default (openrouter mod)
 "api_key_env": "OPENROUTER_KEY",       // optional env var holding a bearer key
 "weight": 2}                           // optional, default 1

// deterministic, offline: starts at 100, loses 50 per violated rule
{"name": "short", "kind": "rule", "max_len": 500, "min_len": 10,
 "require": ["because"], "forbid": ["TODO"]}
```

## Use it

```bash
m judge/create_panel name=pr creator=alice threshold=70 \
    judges='[{"name":"clarity","kind":"llm","prompt":"Is it clear?"},
             {"name":"short","kind":"rule","max_len":500}]'

m judge/judge panel=pr input="Ship the fix because the test now covers the regression."
# → {"approved": true, "average": 82.5, "threshold": 70, "scores": [...], ...}

m judge/panels                    # every panel and its params
m judge/verdicts panel=pr         # the record, every judge's score + reason
m judge/update_panel name=pr creator=alice threshold=80   # creator only
m judge/serve                     # console + API on :51150
m judge/test                      # offline tests (rule judges, throwaway store)
```

HTTP (same functions): reads are GET, writes are POST JSON, at
`/{fn}`, `/judge/api/{fn}` and `/api/judge/{fn}`.

```bash
curl -s localhost:51150/panels
curl -s -X POST localhost:51150/judge -d '{"panel":"pr","input":"..."}'
curl -s localhost:51150/verify?id=3          # re-check a verdict's signatures
curl -s localhost:51150/key_kinds            # the key types judges are issued
```

## Keyrings: multiple key types, quantum-resistant included

Every judge is issued a **keyring** the first time it sits on a panel — one
keypair per available key type, so a vote is signed by several independent
schemes at once (hybrid signing). Tampering with the record means defeating
every scheme, and the record stays attested even after one falls:

| type | scheme | quantum-resistant | notes |
|---|---|---|---|
| `ed25519` | EdDSA / Curve25519 | no | classical leg — fast, tiny sigs, today's interop |
| `ml-dsa-65` | FIPS 204 ML-DSA (lattice) | **yes** | via `dilithium-py` (pure Python) |
| `wots-sha256` | Winternitz + Merkle, SHA-256 | **yes** | pure stdlib — always available; stateful, `2^JUDGE_XMSS_HEIGHT` sigs per key (default 2^8), then the other legs keep signing |

Every vote inside a verdict carries a `sigs` entry per key type (signature +
the public key it was made under), so a verdict is verifiable on its own even
after its panel is removed. `GET /verify?id=N` re-checks everything:
`verified` is true only when **no signature fails and every vote holds at
least one valid quantum-resistant signature**. Panels publish only public
keys (`judges[].keys`, with fingerprints); secrets never leave the local
store. These keys attest the integrity and provenance of this box's verdict
log — the judges run in-process, so this is tamper-evidence, not a claim
that judges are independent parties.

## Trust model (v0.1)

Panel mutation (`create_panel`, `update_panel`, `remove_panel`) is
**loopback-only** over HTTP, and `update/remove` additionally require the
stored `creator` string — the params of the multisig can only be changed from
the box that hosts it. Anyone you share the console with can submit inputs
for judgment and read verdicts. Signed mod-protocol tokens for remote
creators are the natural next step, not in v0.1.

## State

One SQLite file: `~/.mod/judge/judge.db` (override dir with `JUDGE_DIR`).
Verdicts are append-only; removing a panel keeps its verdicts on record.

## Agent judges, panels of panels, and the judge market

Two more judge kinds compose the bench (both live in `market.py`):

- **`agent`** — the judge is an agent speaking the agent protocol
  (orbit/agent). The input is posed as a run (`POST /run` with the judge's
  `agent_type`); the run's finish summary is parsed as
  `{"score": 0-100, "reason": "..."}`. Per-judge knobs: `agent_type`
  (required), `prompt` (required criteria), `model`, `provider`, `steps`
  (default 6), `free` (default true), `url` (default
  `http://localhost:50117/run`, env `JUDGE_AGENT_URL`), `timeout`.
- **`panel`** — another panel sits as one judge: its vote is that panel's
  weighted average, so panels compose into panels of panels of panels.
  The sub-panel's verdict is recorded (and signed) like any other and the
  parent vote's reason cites it by id. A cycle — a panel transitively on
  its own bench — fails closed as a judge error.

The **judge market** is a listing board in the same SQLite file. Anyone can
publish a judge spec (`POST /publish_judge {name, author, spec, description,
tags}`) — a listing is just a spec, it runs nothing until seated. Browse with
`GET /market?q=&kind=`, inspect with `GET /listing?id=`; re-publishing the
same `(name, author)` updates your listing, and only the author can
`POST /unpublish_judge`. `POST /install_judge {id, panel, creator}` seats a
listing on a panel (creator only; bench name clashes auto-suffix). The board
seeds with three starters: `concise` (rule), `clarity` (llm), `reviewer`
(agent).

Panel writes (`create/update/remove_panel`, `install_judge`) answer loopback
callers — or any caller presenting the box's write token (minted once to
`~/.mod/judge/token`, sent as `X-Judge-Token`), which is how the console's
PANELS and MARKET tabs work through the gateway. The console's agent-type
picker reads `GET /agents`, a cached proxy of the agent-protocol roster.
