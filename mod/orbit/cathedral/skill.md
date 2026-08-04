---
name: cathedral
description: Rent confidential compute from cathedral.computer — attested TDX CPU jobs, sealed persistent workers, and confidential GPU executions with signed receipts. BYOK; every run bills the caller's own prepaid credits.
type: orbit-module
---

# cathedral

Run a workload inside attested hardware and get back the result plus a signed receipt binding
the evidence, workload, result, charge, and teardown. Upstream is
[cathedral.computer](https://cathedral.computer/docs/).

## Billing rule — do not work around it

Every paid call is charged to **the caller's own** prepaid Cathedral credits. This module holds
no house key and must never front anyone's compute:

- Local key order: explicit arg → `~/.mod/cathedral/keys.json` for the named account →
  `CATHEDRAL_API_KEY` (default account only, so naming an account can't silently bill someone else).
- The HTTP gateway requires a per-request `cat_sk_*` and returns `403` if a caller presents the
  server's own ambient key.
- `topup` returns Cathedral's hosted Stripe URL — the payer's card, the payer's credits.
- Keys are never logged. Payers appear as `cat:<sha256[:12]>` fingerprints.

Anything above **$0.20** needs `yes=1` (CLI/Python) or `confirm: true` (HTTP). Read the price
out loud before running: `estimate` and `prices` exist for exactly that.

## Capabilities

- Live catalog with runtime gates (`profiles`, `gpu_ready`)
- Credits: balance, packs, hosted checkout, post-payment verification
- One free restricted minute per verified account (`free_test`)
- `attest.v1` one-shot TDX CPU job (`run`) — $0.20 per completed execution
- `custom.v1` persistent sealed worker (`rent`) — hourly, optional hybrid-GPU preview
- `cc_gpu` confidential GPU execution (`gpu`) — $3.00, refused unless every catalog gate PASSes
- Worker lifecycle (`workers`, `worker`, `wait`, `stop`) and signed receipts (`receipt`)
- Local per-key spend ledger (`ledger`)
- A web console (`app/`, port 50391) over the same API — catalog and gates, spend quoted before
  submit, jobs, workers, receipts, credits

## Usage

### CLI

```bash
m cathedral                                   # who pays, balance, prices
m cathedral/login cat_sk_...
m cathedral/estimate profile=custom.v1 shape=8x32 minutes=90
m cathedral/free_test
m cathedral/run image=python:3.12-slim command='print(6*7)' wait=1
m cathedral/rent image=nginx:1.27-alpine minutes=60 max_spend_usd=1 yes=1
m cathedral/gpu command='["python3","-c","print(1)"]' yes=1
m cathedral/receipt wrk_... save=~/receipt.json
m cathedral/ledger
```

### Python

```python
import mod as m
cat = m.mod('cathedral')(account='alice')     # alice's key, alice's credits
job = cat.run(image='python:3.12-slim', command=['python', '-c', 'print(6*7)'])
cat.wait(job['worker_id'])
cat.receipt(job['worker_id'])
```

### HTTP (BYOK, port 50390)

```bash
curl localhost:50390/prices                                  # public
curl localhost:50390/me   -H "Authorization: Bearer cat_sk_..."
curl localhost:50390/run  -H "Authorization: Bearer cat_sk_..." \
     -H 'Content-Type: application/json' \
     -d '{"image":"python:3.12-slim","command":["python","-c","print(6*7)"]}'
```

### Console (port 50391, published at `{host}/cathedral`)

```bash
m cathedral/serve            # api :50390 + console :50391, both under pm2
m cathedral/app              # its URLs
```

The console holds the key in the browser tab (`sessionStorage`) and sends it to
`/cathedral/_api` — a same-origin alias `app/serve.py` forwards to the BYOK gateway, with an
allowlist of headers and nothing stored or logged. It sends `confirm: true` only when the payer
ticks the authorization box; otherwise `false`, so a moved price gets refused upstream rather
than silently approved.

## Gotchas

- A one-shot worker is `completed` only after fresh-environment deletion is confirmed — poll,
  don't assume.
- `425` on a receipt means execution or cleanup is still pending, not an error.
- The free test is an account entitlement, not reusable credit: rotating keys does not reset
  it, and a second attempt returns `409`.
- Worker creation is idempotency-keyed. Retry a timeout with the *same* key and *same* body;
  a different body under the same key is rejected.
- Confidential GPU verifies NVIDIA CC mode only — AMD SEV host evidence is **not** attested,
  and hybrid GPU is provider-trusted with plaintext inputs at the GPU host. Don't describe
  either as fully confidential.
- `/v1/keys` management needs the human website session and is not wrapped here by design.
- The gateway keeps the `/cathedral` prefix for the app and strips `/api/cathedral` for the API;
  `app/serve.py` strips its own prefix and must never redirect the bare form, or the published
  URL loops against the gateway's 308.
