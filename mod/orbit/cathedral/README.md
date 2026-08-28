# cathedral

Rent confidential compute from [cathedral.computer](https://cathedral.computer) as a mod.

Cathedral runs your workload inside attested hardware — an Intel TDX CPU enclave, or a
confidential NVIDIA GPU — and hands back the result together with a **signed receipt** that
binds the verified hardware evidence to the workload, the result, the charge, and the
teardown. This module is the mod-protocol front door: catalog, credits, workers, receipts,
from Python, the CLI, or HTTP.

## Whose credits get spent

**Every paid call is billed to the caller's own Cathedral account.** That is the whole
design, not a footnote:

- **Locally**, the key resolves from an explicit argument → this machine's off-tree vault at
  `~/.mod/cathedral/keys.json` (0600, never committed) for the named account → `CATHEDRAL_API_KEY`.
  The env var only answers for the `default` account: naming an account and silently falling
  back to ambient credentials would charge the wrong person.
- **Over HTTP** (`api/api.py`), the caller sends their own `cat_sk_*` on every request and the
  server signs the upstream call with exactly that key. There is no house key. If the process
  happens to have `CATHEDRAL_API_KEY` set and a caller presents it, the request is refused
  (`403`) rather than served on the server's balance.
- **Top-ups** go through Cathedral's own Stripe checkout in the payer's name. `topup` only
  hands you the hosted URL; no card ever touches this module.
- Keys are never logged or echoed. Anything that identifies a payer — ledger rows, `/me`,
  `pays` fields — carries a `cat:<sha256[:12]>` fingerprint instead.

A local ledger at `~/.mod/cathedral/ledger.json` records every job launched from this machine
against its payer's fingerprint, so "what did this key spend, and on what" is always
answerable offline. Cathedral remains the source of truth for the balance.

## Spend guard

Anything that can cost more than **$0.20** needs an explicit `yes=1` (CLI/Python) or
`confirm: true` (HTTP) — the same line Cathedral's own agent contract draws. Without it you
get a `confirmation required` response naming the price and the payer, and nothing is
submitted.

## Getting a key

Key issuance is free; paid execution needs prepaid credits on that same account.

1. Sign in at <https://cathedral.computer/account/> (Google or an email link).
2. Create a scoped `cat_sk_*` key. Cathedral shows the secret **once** and stores only its
   SHA-256 hash.
3. `m cathedral/login cat_sk_...` — stored 0600 under `~/.mod/cathedral/`, verified against
   `/v1/credits` before it is written.

Key management routes (`GET|POST /v1/keys`, `DELETE /v1/keys/{id}`) require the human website
session and are deliberately **not** reachable with an API key, so this module does not wrap
them. Create and revoke keys in the dashboard.

## CLI

```bash
m cathedral                                   # who pays, what's in the account, what it costs
m cathedral/login cat_sk_...                  # store a key on this machine
m cathedral/accounts                          # fingerprints only, never secrets
m cathedral/credits                           # your prepaid USD balance
m cathedral/packs                             # buyable credit packs
m cathedral/topup pack_10                     # -> hosted Stripe URL, your card
m cathedral/verify_payment cs_...             # settle the redirect race after paying

m cathedral/prices                            # the published price sheet
m cathedral/estimate profile=custom.v1 shape=8x32 minutes=90
m cathedral/inventory                         # every hardware class and shape you can order
m cathedral/profiles                          # live catalog — these fields are runtime gates
m cathedral/gpu_ready                         # are the confidential-GPU gates PASS right now

m cathedral/free_test                         # one free restricted minute (once per account)
m cathedral/run image=python:3.12-slim command='print(6*7)'
m cathedral/run image=python:3.12-slim command='print(6*7)' wait=1
m cathedral/rent image=nginx:1.27-alpine minutes=60 max_spend_usd=1 yes=1
m cathedral/gpu command='["python3","-c","import torch;print(torch.cuda.is_available())"]' yes=1

m cathedral/workers                           # your workers
m cathedral/worker wrk_...
m cathedral/wait wrk_...                      # poll to a terminal state
m cathedral/stop wrk_...                      # tear down
m cathedral/receipt rcpt_... save=~/receipt.json
m cathedral/ledger                            # what this machine has spent, and on whose key
```

## Python

```python
import mod as m

cat = m.mod('cathedral')(account='alice')     # alice's key, alice's credits
cat.credits()
cat.estimate('attest.v1')                     # {'usd': 0.2, 'unit': 'completed execution', ...}

job = cat.run(image='python:3.12-slim', command=['python', '-c', 'print(6*7)'])
done = cat.wait(job['worker_id'])
cat.receipt(job['worker_id'], save='~/receipt.json')
```

## HTTP

```bash
m cathedral/serve                             # api on :50390, console on :50391
m cathedral/serve_api                         # just the gateway
m cathedral/app                               # where the console lives
curl localhost:50390/prices                   # public
curl localhost:50390/me       -H "Authorization: Bearer $CATHEDRAL_API_KEY"
curl localhost:50390/run      -H "Authorization: Bearer $CATHEDRAL_API_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"image":"python:3.12-slim","command":["python","-c","print(6*7)"]}'
```

Public, no key: `/`, `/health`, `/prices`, `/profiles`, `/inventory`, `/gpu/ready`,
`/credits/packs`. Everything else requires the caller's own key.

## What compute is there

`/profiles` answers "what profiles exist" and buries the hardware inside them: the confidential
GPU is a hardware class rather than a profile, the hybrid preview sits beside it, and the four
sealed-worker sizes are down in `custom.v1.resources`. Read literally, it shows two things and
hides a 96 GiB GPU and five buyable shapes.

`inventory` (CLI) / `GET /inventory` (HTTP) flattens the same catalog into one row per hardware
class — its shapes, prices, the endpoint that orders each, and, where a class is shut, the
gate holding it shut. Nothing is invented: what the catalog does not state comes back `null`.
The console's Catalog tab is this list, and it is the tab it opens on.

```
$ m cathedral/inventory
3 hardware classes · 7 shapes · 6 orderable · $0.20–$4.40 · 1 live, 1 preview, 1 unavailable
```

## Profiles

| profile | what it is | price |
| --- | --- | --- |
| free test | one fixed restricted minute, once per verified account — no custom workload, egress, files, artifacts, secrets, or reuse | $0 |
| `attest.v1` | fresh bounded TDX CPU job, one shot, teardown-bound receipt | $0.20 / completed execution (failed CPU runs refunded) |
| `custom.v1` | persistent sealed TDX CPU worker, verified boot, SSH | $0.40–$4.40 / running hour by shape |
| `custom.v1` + `gpu=` | hybrid GPU **preview** — provider-trusted, not confidential GPU | quoted, all-in, before both sides are ready |
| `cc_gpu` | confidential GPU on `gcp-g4-rtx-pro-6000-sev-v1`, Spot | $3.00 / verified execution, up to 10 workload minutes |

One-shot profiles bill per execution, never by elapsed time. Hourly metering applies only to
an explicitly persistent worker, starts once the requested workload is ready, and stops after
provider cleanup; an existing worker keeps the rate stored at creation. Storage and egress are
not included unless quoted.

## Trust boundary — read this before believing a receipt

- **TDX CPU**: fresh hardware evidence must satisfy the requested policy before the workload
  starts, and a one-shot only reaches `completed` after fresh-environment deletion is confirmed.
- **Confidential GPU**: Cathedral verifies NVIDIA GPU confidential-compute mode and binds the
  workload and result. It does **not** collect or verify AMD SEV host attestation, and for G4
  receipts `report_data_match` and `intel_verified` are null. `gpu()` refuses to submit unless
  the live catalog reports `availability=available`, `customer_enabled=true`,
  `cathedral_evidence_status=PASS`, `verifier_log_digest_evidence_status=PASS`, and a canonical
  `live_evidence_digest` — Cathedral's stated precondition, enforced here rather than assumed.
- **Hybrid GPU**: transport between the TDX controller and the remote GPU is encrypted, but
  inputs become plaintext to the trusted GPU host. The provider and GPU memory are trusted.
  This is a preview, not confidential GPU.
- **Attestation-gated secret release** is planned upstream and not enabled during live testing.
- The offline verifier proves a trusted Cathedral key signed the exact assertions and that the
  document is internally consistent. It does not replay Intel or NVIDIA evidence, inspect
  billing, contact the provider, or confirm teardown.

## Verifying a receipt

A receipt is only evidence once the signature checks out, so verification ships here rather
than being left to a CLI you have to install. It needs no API key — the receipt is the whole
input, which is what makes it worth keeping after the worker, the key and the credits are gone.

```bash
m cathedral/verify rcpt_...              # fetch with your key, then check the signature
m cathedral/verify path=~/receipt.json   # check one you already saved
m cathedral/trusted_keys                 # the pinned ed25519 signing keys
```

```bash
curl -X POST http://localhost:50390/receipts/verify -d @receipt.json   # public, no key
curl http://localhost:50390/receipts/trusted-keys
```

Cathedral's published keys live at `/customer-receipt-trusted-keys.json`, and their advice is
to pin that file through a channel you trust before relying on it. The first fetch is written
to `~/.mod/cathedral/trusted-keys.json` and later fetches may only *add* key ids: if the public
key for an id you already pinned ever changes, the pinned copy is kept and the change is
reported. That is either a rotation Cathedral should have published under a new id, or somebody
swapping the key you verify against — neither should pass in silence.

Cathedral documents the algorithm (ed25519) and what is covered ("every top-level assertion
except the signature object") but not the byte encoding those assertions are serialized to
before signing. Rather than guess one and report a false negative as tampering, the verifier
tries the standard encodings and names the one that matched. A receipt matching none comes back
`signature: "unverified"` with the list of what was tried — never `verified`, and never
"forged".

## Errors

Cathedral's failure contract is passed through unflattened: `400` malformed, `401` missing or
revoked key, `402` insufficient prepaid credits, `409` capability unavailable or worker-state
conflict (a second free test lands here), `422` valid JSON that violates the profile contract,
`425` receipt not publishable yet, `503` capacity or billing service unavailable.

Requests that create a worker carry an `Idempotency-Key`, returned to you in the response.
After a network timeout, retry with the **same** key and the **same** body — a different body
under the same key is rejected.

## Console

`app/` is a zero-dependency web console for the same API — no build step, no npm, two static
files and a 150-line server.

```bash
m cathedral/serve                             # api on :50390, console on :50391
open http://localhost:50391/cathedral         # published at {host}/cathedral
```

It shows the live catalog with each GPU gate marked PASS or FAIL, quotes the spend guard
**before** you submit rather than after the round trip, submits CPU jobs / sealed workers /
GPU executions, lists workers with their state, opens signed receipts (and downloads them),
and walks a credit top-up out to Cathedral's hosted checkout.

Whose key: yours, and only in your browser. The console keeps it in `sessionStorage` — this
tab, this session, gone when you close it — and sends it as an `Authorization` header to
`/cathedral/_api`, the same-origin alias this box forwards to the BYOK gateway. The app server
stores nothing, logs no headers, and forwards an allowlist (`Authorization`, `x-cathedral-key`,
`Content-Type`, `Idempotency-Key`) so ambient browser state like cookies never crosses the hop.
Below the confirmation threshold the console sends `confirm: false`: if a price has moved, the
API refuses and asks, instead of this page authorizing a spend nobody saw.

Both halves follow the protocol's URL rule — `{host}/api/cathedral` → the API on 50390,
`{host}/cathedral` → the console on 50391 — so `m caddy/apply` routes them with no per-module
config.

## Not built

`/v1/keys` management is website-session only by Cathedral's design and stays out of scope.

## Layout

```
cathedral/
├── config.json          # ports 50390/50391, BYOK contract, price sheet, fns
├── mod.py               # Mod class — CLI + Python, key vault, spend guard, ledger
├── api/api.py           # FastAPI BYOK gateway — per-request key, no house key
├── api/requirements.txt
├── app/index.html       # the console
├── app/app.css
├── app/app.js           # key in the tab, spend quoted before submit
├── app/serve.py         # static bundle + the /cathedral/_api alias (stdlib only)
├── skill.md
├── test/test_cathedral.py
└── test/test_app.py     # console server, against a stub upstream
```

Layered on: <https://cathedral.computer/docs/>
