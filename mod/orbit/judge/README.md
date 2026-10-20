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

Every model judge is an agent spoken to over the agent protocol
(`GET /agents` + `POST /run/stream`). A judge names a `provider` — any mod on
this box whose config.json declares an `agent_contract` (`GET /providers`
lists them: this module, agent, claudecode, hermes, zkapi …) — and an
`agent_type` off that provider's roster (`GET /roster?provider=`). The input is
posed as a run; the finish summary is parsed as `{"score", "reason"}`.

```jsonc
// an agent judge. No provider/agent_type = judge/scorer, this module's own
// agent: one turn on the LFM weights via liquidai (keyless, ~30-60s on CPU).
{"name": "clarity", "kind": "agent", "prompt": "Is the text clear and specific?",
 "provider": "judge", "agent_type": "scorer",  // or e.g. "claudecode"/"claude", "agent"/"default"
 "model": "LiquidAI/LFM2.5-1.2B-Instruct",     // optional, passed to the run
 "chat_url": "https://host/v1/chat/completions", // scorer only: OpenAI-compatible endpoint instead of liquidai
 "api_key_env": "OPENROUTER_KEY",              // scorer only, with chat_url
 "steps": 3,                                   // remote providers: run budget
 "weight": 2}                                  // optional, default 1

// "kind": "llm" is a legacy alias — saved as the agent judge above on
// judge/scorer (its `url` becomes `chat_url`). Old benches still run.

// deterministic, offline: starts at 100, loses 50 per violated rule
{"name": "short", "kind": "rule", "max_len": 500, "min_len": 10,
 "require": ["because"], "forbid": ["TODO"]}
```

## Use it

```bash
m judge/create_panel name=pr creator=alice threshold=70 \
    judges='[{"name":"clarity","kind":"agent","prompt":"Is it clear?"},
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
# try one judge spec without a panel — its 0-100 score + reason, nothing recorded (trusted callers)
curl -s -X POST localhost:51150/score -d '{"spec":{"kind":"agent","prompt":"is it clear?"},"input":"..."}'
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

## Who is calling (v0.3): sign in with the mod protocol

Panel changes (`create_panel`, `update_panel`, `remove_panel`, `add_judge`,
`install_judge`, `score`) need a caller who has said who they are. There are
two doors, both in `identity.py` / `serve.py`:

| standing | how | what it may do |
|---|---|---|
| **box** | loopback with no proxy header, or the box token (`~/.mod/judge/token`, sent as `X-Judge-Token`) | anything; `creator` passes through as given — the CLI path |
| **signed** | a **mod-protocol token** as `Authorization: Bearer <token>` — `m.mod('auth')().token({})` on a box holding your key, or one `personal_sign` from a browser wallet (the console's *connect wallet*) | creates panels and publishes judges **as its address**: on every write `creator` / `author` is set to the signer, so update/remove/add_judge/install go through the panel's own creator check against that address |
| anon | nothing | reads everything, submits inputs for judgment, browses the market |

The token is verified by the fleet's one shared auth (`m.mod('auth')`), the
same envelope every other module accepts, so a wallet, the `m` CLI and a peer
module all arrive at one lowercase address. `GET /auth` (alias `/whoami`) says
who the box thinks you are: `{role: box|signed|anon, address}`. Tokens are
good for `JUDGE_TOKEN_MAX_AGE` seconds (default 7 days).

```bash
TOKEN=$(python3 -c "import mod as m; print(m.mod('auth')().token({'mod':'judge'}))")
curl -s localhost:51150/whoami -H "Authorization: Bearer $TOKEN"
curl -s -X POST localhost:51150/create_panel -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"pr","judges":[{"name":"clarity","kind":"agent","goal":"approve only clear, specific writing"}]}'
# → creator = your address, whatever the body said
```

## A judge's goal, adding judges one at a time, and their keys

Every judge takes an optional **`goal`** — one line on what this judge is
judging *for*, set by the panel's creator (`"approve only PRs that keep the
API backwards compatible"`). For agent judges it is put before the
model as `Goal: …` ahead of the criteria `prompt`, and either may stand alone
(a goal is enough to seat an agent judge). Rule and panel judges carry it as
documentation; the console shows it under every judge.

`POST /add_judge {name, creator, judge}` seats one more judge on an existing
panel without resending the bench — the same checks as create time, and the
quorum stays where it was. The console's **+ add judge** on each panel is
this call.

Every judge's keyring is public: `GET /judge_keys?panel=&judge=` (also inline
in `/panel` and `/panels` as `judges[].keys`) returns, per key type, the
public key, its fingerprint, the algorithm and whether it is
quantum-resistant. The console shows the fingerprints under each judge; click
one for the full public key and a copy button.

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
seeds with three starters: `concise` (rule), `clarity` (agent, judge/scorer), `reviewer`
(agent).

Panel writes (`create/update/remove_panel`, `install_judge`) answer loopback
callers — or any caller presenting the box's write token (minted once to
`~/.mod/judge/token`, sent as `X-Judge-Token`), which is how the console's
PANELS and MARKET tabs work through the gateway. The console's agent-type
picker reads `GET /agents`, a cached proxy of the agent-protocol roster.
