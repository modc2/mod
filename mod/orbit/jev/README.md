# jev · typed questions over the Jev model

A mod that wraps **completely over jevgo** — `github.com/fgn/jevgo`, the Go
client for TypeSafe AI's System One API and its flagship model, **Jev**.

System One is not a chat API. You hand it a *state* (any JSON-serializable
content) plus named, typed questions, and it answers each one with a
probability distribution:

| kind     | you ask                              | you get back                                          |
|----------|--------------------------------------|-------------------------------------------------------|
| `noul`   | a yes/no question                    | probability of yes (0–1)                               |
| `choice` | pick one of your options             | chosen option, confidence, per-option probabilities    |
| `score`  | rate against ordered levels          | weighted position, confidence, per-level probabilities |
| `raw`    | pass-through for unknown kinds       | whatever the API returns                               |

## Why a Go bridge

"Wraps completely" is literal: every network call goes through one static
binary built from `src/cmd/jevbridge`, which imports jevgo. Client
construction, functional options, retries (with `Retry-After`), timeouts and
the sentinel error taxonomy are all jevgo's — `mod.py` only marshals JSON in
and out. The Python side can never drift from the Go protocol client.

jevgo errors surface as `{error, code}` with stable codes:
`authentication`, `permission_denied`, `not_found`, `unprocessable`,
`rate_limit`, `overloaded`, `bad_request`, `internal`, `timeout`,
`connection`, `response_too_large`, `invalid_request`, `invalid_config`.

## Setup

```sh
export TYPESAFE_API_KEY=...        # required; env only, never stored in this module
# optional: TYPESAFE_BASE_URL (default https://api.typesafe.ai)
#           TYPESAFE_DEFAULT_MODEL (default jev-latest)
m jev/build                        # go mod tidy + go build -> src/bin/jevbridge
```

## Use

```sh
m jev                              # status: built? key present? base url, model
m jev/noul "refund demand, third email" instructions="Is the customer angry?"
m jev/choice "I was charged twice" instructions="Which team should handle this?" \
    options=billing,technical,other
m jev/score "I was charged twice!!" instructions="How frustrated is the customer?" \
    levels=Calm,Frustrated,Furious
m jev/models                       # every model the API offers
m jev/test                         # offline: build, info op, wire validation
```

The full SystemOne surface — many questions in one call, model override,
per-call timeout/retries, `extra` pass-through fields:

```sh
m jev/ask state="I was charged twice. Fix this ASAP." questions='{
  "urgent":      {"kind": "noul",   "instructions": "Does this convey urgency?"},
  "department":  {"kind": "choice", "instructions": "Which team?",
                  "criteria": {"billing": null, "technical": null, "other": null}},
  "frustration": {"kind": "score",  "instructions": "How frustrated?",
                  "criteria": ["Calm", "Frustrated", "Very angry"]}
}' model=jev-latest timeout_s=20
```

Answers come back keyed by question name, each tagged with its `kind`, plus
`model`, `usage` (input/output tokens), `request_id` and `attempts`.

## Layout

```
config.json                  module manifest (fns, env contract, notes)
mod.py                       anchor — Mod class, thin shells onto the bridge
src/go.mod                   module jevbridge; requires github.com/fgn/jevgo
src/cmd/jevbridge/main.go    the whole protocol surface, ~200 lines
src/bin/jevbridge            built artifact (gitignored territory; m jev/build)
```

jevgo is MIT-licensed and a community client, not affiliated with TypeSafe.
