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
```

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
