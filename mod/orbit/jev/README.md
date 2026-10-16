# jev · typed questions over the Jev model, paid in crypto

A mod that wraps **completely over jevgo** — `github.com/fgn/jevgo`, the Go
client for TypeSafe AI's System One API and its flagship model, **Jev** — and
serves it over the mod protocol so anyone can ask and **pay per question in
USDC with no account and no KYC**.

System One is not a chat API. You hand it a *state* (any JSON-serializable
content) plus named, typed questions, and it answers each one with a
probability distribution:

| kind     | you ask                              | you get back                                                  |
|----------|--------------------------------------|---------------------------------------------------------------|
| `noul`   | a yes/no question (+ optional true/false criteria) | probability of yes (0–1)                        |
| `choice` | pick one of your options             | chosen option, confidence, per-option probabilities            |
| `score`  | rate against ordered levels          | weighted position, level, confidence, per-level probabilities  |
| `raw`    | pass-through for unknown kinds       | whatever the API returns                                       |

## Why a Go bridge

"Wraps completely" is literal: every network call goes through one static
binary built from `src/cmd/jevbridge`, which imports jevgo. Client
construction, every functional option, retries (with `Retry-After`),
timeouts, custom headers, slog logging, tracing and the sentinel error
taxonomy are all jevgo's — `mod.py` only marshals JSON in and out. The Python
side can never drift from the Go protocol client.

Bridge ops: `system_one`, `models`, `encode` (validate offline and return the
exact wire body — never sends), `info`, `version`.

Every jevgo option is reachable from any call:

```
model, timeout_s, max_retries,
retry={max_retries, initial_backoff_ms, max_backoff_ms, jitter, statuses,
       respect_retry_after, max_retry_after_ms, connection_errors, timeouts, total_timeout_ms}
headers={...}, log_level=debug|info|warn|error|off,
include_headers, include_raw, trace, extra={...}
```

jevgo errors surface as `{error, code}` (plus `status`, `message`,
`request_id`, `retry_after_ms` for API errors) with stable codes:
`authentication`, `permission_denied`, `not_found`, `unprocessable`,
`rate_limit`, `overloaded`, `bad_request`, `internal`, `timeout`,
`connection`, `response_too_large`, `invalid_response`, `invalid_request`,
`invalid_config`.

## Setup

```sh
export TYPESAFE_API_KEY=...        # required; env only, never stored in this module
# optional: TYPESAFE_BASE_URL, TYPESAFE_DEFAULT_MODEL (jev-latest), TYPESAFE_LOG_LEVEL
m jev/build                        # go mod tidy + go build -> src/bin/jevbridge
m jev/pricing pay_to=0xYourAddress # where USDC lands; price_usd=0.001 networks=base
m jev/serve background=1           # console + API + MCP on :50127 under pm2
```

## Use (owner, from this box — free)

```sh
m jev                              # status: built? key? price? server?
m jev/noul "refund demand, third email" instructions="Is the customer angry?"
m jev/choice "I was charged twice" instructions="Which team should handle this?" \
    options=billing,technical,other
m jev/score "I was charged twice!!" instructions="How frustrated is the customer?" \
    levels=Calm,Frustrated,Furious
m jev/ask state="..." questions='{"urgent":{"kind":"noul","instructions":"Urgent?"}}' trace=1
m jev/encode state="hi" questions='{"q":{"kind":"noul","instructions":"greeting?"}}'   # offline
m jev/models · m jev/info · m jev/version · m jev/test
```

## Use (anyone, over HTTP — paid)

The protocol's URL rule: `/jev/` is the console, `/jev/api/{fn}` the API
(`/api/jev/{fn}` is the permanent alias), `/jev/mcp` the MCP server.

```sh
curl -X POST https://modc2.com/jev/api/noul -H 'content-type: application/json' \
  -d '{"state":"I was charged twice","instructions":"Is the customer angry?"}'
# → 402 {x402Version:1, accepts:[{scheme:exact, network:base, maxAmountRequired:"1000",
#         payTo:0x…, asset:USDC, extra:{name,version}}], price:{usd:0.001}, kyc:"none"}
```

Two ways to pay, both without an account:

1. **x402, per call.** Sign the offer with any EVM wallet — an EIP-3009
   `transferWithAuthorization` for exactly the price (EIP-712, no gas; the
   facilitator submits it) — and retry with `X-PAYMENT: <base64>`. The
   facilitator verifies *before* the question runs and settles *after* it
   answered: a failed answer is never charged, a used nonce is refused.
   The receipt comes back in `X-PAYMENT-RESPONSE`. The console does this
   with the browser wallet; the x402 module's `x402_call` does it for agents.
2. **Credits, prepaid.** `POST /jev/api/topup` with an `X-PAYMENT` of any
   amount ≥ `min_topup_usd`. The *paying* address is credited — the payment
   is the proof. Spend by sending a **mod-protocol token** for that address
   (`token:` header or `Authorization: Bearer`): a wallet signature over
   `{"data":"jev","time":"<unix secs>"}`, base64url of
   `{data,time,key,signature}`. One signature per session, no transactions.

Free routes: `health`, `info`, `price`, `models`, `encode`, `whoami`,
`balance`, `tools`, `stats`. Calls from this box itself (loopback, nothing
forwarded) are the owner's and free; `JEV_FREE=1` makes everyone free.

`ask` is priced per question (`max_questions` per call). `payment` in every
answer says what happened: `free`, `credits` (with `left_usd`) or `x402`
(with `tx`, `network`, `settled`).

### MCP

`POST /jev/mcp` (stateless streamable HTTP). Tools: `jev_noul`, `jev_choice`,
`jev_score`, `jev_ask` (paid — same headers), `jev_models`, `jev_price`,
`jev_balance`, `jev_encode` (free). A paid tool without payment returns
`isError` with the 402 body as its content.

### Owner

```sh
m jev/pricing                                  # the full policy + where it lives
m jev/pricing price_usd=0.002 networks=base,polygon facilitator=https://…  free_local=0
m jev/grant 0xFriend usd=1                     # credits for free
m jev/balance 0xFriend · m jev/ledger · m jev/payments · m jev/stats
```

`pay_to` and overrides live in `~/.mod/jev/pay.json` (or `JEV_PAY_TO`); the
ledger in `~/.mod/jev/pay.db`. Nothing private is in `config.json`. The
default facilitator is PayAI (no key needed, mainnet Base and others);
`https://x402.org/facilitator` works for `base-sepolia`.

The same rules plug into the protocol's own server: `Mod().paywall` has the
`gate_check(fn, headers)` interface that `m.serve(..., paywall=)` expects.

## Layout

```
config.json                  manifest (port 50127, route, endpoints, public pay defaults)
mod.py                       anchor — Mod class, thin shells onto the bridge + pay fns
jevpay.py                    x402 seller side, credits ledger, mod-token identity, Gate
serve.py                     console + /jev/api + /jev/mcp on one port (pm2 `jev`)
web/index.html               the console: ask, pay per call with a wallet, credits
tests/test_jev.py            offline: gate, replay, credits, identity, routing, MCP
src/go.mod                   module jevbridge; requires github.com/fgn/jevgo
src/cmd/jevbridge/main.go    the whole jevgo surface, one file
src/bin/jevbridge            built artifact (m jev/build)
```

jevgo is MIT-licensed and a community client, not affiliated with TypeSafe.
