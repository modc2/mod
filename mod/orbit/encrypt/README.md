# encrypt

**Encrypted messages whose cryptography you bring yourself.**

This module ships no cipher. You upload a *circuit* — a python file that
implements encryption — and the module runs it server-side inside a sandbox to
turn your plaintext into ciphertext. The ciphertext is stored in the
[`store`](../../core/store) mod under **your** wallet identity and comes back as
a CID. The key is used for the length of one request and is never written down.

Everything you bring, you can take back and destroy: circuit sources download,
ciphertext downloads, and delete reaches through to the store object.

```
you ──plaintext + key + circuit──► encrypt api ──runs your circuit in a sandbox──►
      ciphertext ──► store mod (your identity, your quota, your ACL) ──► CID
```

---

## The circuit contract

A circuit is one python file:

```python
def encrypt(data: bytes, key: bytes, params: dict) -> bytes: ...
def decrypt(data: bytes, key: bytes, params: dict) -> bytes: ...
```

`key` is whatever passphrase the caller sent, as bytes — stretching it into a
real key (scrypt, PBKDF2, …) is the circuit's job, and so is the wire format:
salts, nonces and tags live inside the bytes you return. `params` is an optional
public dict stored alongside the message and handed back on decrypt, so a
circuit can be tuned per message without the server understanding it.

Two reference circuits ship in [`circuits/`](circuits) — **not installed**, they
are examples to read and replace:

| circuit | what it is | needs |
|---|---|---|
| `aes_gcm.py` | scrypt → AES-256-GCM, authenticated | `cryptography` |
| `chacha_poly.py` | PBKDF2 → ChaCha20 + HMAC-SHA256, encrypt-then-MAC | stdlib only |

Nothing is accepted on trust: every upload must pass a **roundtrip selftest** in
the sandbox — `decrypt(encrypt(x)) == x`, and `encrypt(x) != x` so a
pass-through "cipher" can't quietly store your plaintext.

A circuit's id is the sha256 of its source, so the same file uploaded twice is
the same circuit: re-uploading is an update, not a duplicate, and a message can
always name the exact code that produced it.

## The sandbox

Circuits are user code, so they run in a short-lived child process
([`encryptor/runner.py`](encryptor/runner.py)) with:

- **no network** — `unshare -n`, an empty network namespace, so a circuit cannot
  send your key anywhere
- **no privileges** — drops to `nobody` when the API runs as root
- **no filesystem writes** — `RLIMIT_FSIZE = 0`
- **cpu, memory and wall-clock limits** — killed, not waited on
- **no repo on the path** — `python3 -I -B`, scrubbed env; the standard library
  and system packages only

`GET /status` reports which of these are actually in force on this host, so a
degraded sandbox is visible instead of assumed.

## Quick start

```bash
m encrypt/serve                                  # api :50380 + console :50381
m encrypt/examples                               # the reference circuits
m encrypt/add_circuit circuits/chacha_poly.py    # bring one (validated + pinned)
m encrypt/circuits                               # → id c8e1f…, store cid Qm…

m encrypt/encrypt "meet at nine" circuit=c8e1f… key=hunter2 label=note
m encrypt/messages                               # ciphertext, by CID
m encrypt/open m63aff… key=hunter2               # decrypt server-side
m encrypt/download m63aff… out=./note.enc        # raw ciphertext, decrypt yourself
m encrypt/rm m63aff…                             # delete server-side
```

The console at `/encrypt` (or `localhost:50381`) does the same thing with a
MetaMask sign-in: bring a circuit, compose, open, download, burn, delete.

## API

Auth is the fleet's protocol token — a wallet-signed `{data, time, key,
signature}` envelope in `Authorization: Bearer <token>`, verified by
`m.mod('auth')`. The **same token is forwarded to the store**, so the store's
whitelist, quota and terms apply and every object is owned by the caller, not by
this module.

| | |
|---|---|
| `GET /health` · `GET /status` · `GET /me` | liveness, sandbox capabilities, store identity |
| `GET /circuits` | my circuits + public ones |
| `POST /circuits` · `POST /circuits/upload` | bring a circuit (JSON source, or a `.py` upload) |
| `POST /circuits/install` | install one shared by store CID |
| `GET /circuits/{id}` · `GET /circuits/{id}/source` | metadata · download the source |
| `DELETE /circuits/{id}[?force=1]` | delete server-side (refused while messages need it) |
| `GET /messages` · `GET /messages/{id}` | my messages (metadata only) |
| `POST /messages` | encrypt + store — `{circuit, key, text\|data_b64, label, public, burn, params}` |
| `POST /messages/attach` | register a blob you encrypted yourself — `{cid, circuit, label}` |
| `POST /messages/{id}/open` | decrypt server-side — `{key}` |
| `GET /messages/{id}/download[?burn=1]` | raw ciphertext |
| `POST /messages/{id}/publish` | flip the ciphertext public/private in the store |
| `DELETE /messages/{id}` · `DELETE /messages?confirm=true` | delete one · purge everything I own |

## What is kept, and where

Off-tree under `~/.mod/encrypt/` (override with `ENCRYPT_DIR`):

```
circuits/<id>.py    the circuit source you brought
circuits.json       {id: {name, owner, sha256, public, cid, selftest}}
messages.json       {id: {owner, circuit, cid, bytes, label, burn, params}}
```

What is **not** kept anywhere: keys, passphrases, plaintext. A message row holds
a CID, a circuit id and a label — nothing in this module can decrypt anything
without the key you type.

If you want the server never to hold the plaintext at all, encrypt locally,
upload the ciphertext to the store yourself, and register it with
`POST /messages/attach`. You keep the same list / download / delete surface with
the server holding nothing but a pointer.

## Honest limits

- **Server-side encryption means the server sees the plaintext and the key for
  the length of the request.** That is the trade for running a circuit you can
  swap out. `attach` is the mode where it doesn't.
- **Delete is as complete as the store makes it.** `DELETE /messages/{id}`
  removes the metadata row and calls the store's `/rm`. Today the store drops
  its index entry but its localfs backend keeps the bytes, so anyone already
  holding the CID can still fetch them — the delete response says so
  (`store_removed`) rather than claiming the bytes are gone. It becomes a true
  delete the day `dstore.rm()` also calls `localfs.rm(cid)`.
- **A circuit is only as good as its author.** The selftest proves it round-trips,
  not that it is secure. `chacha_poly.py` is hand-rolled crypto and is here to be
  dependency-free and readable, not to protect anything that matters.
- **Burn-after-read** deletes on the first successful open or download. If the
  request dies between the store fetch and your screen, the copy is gone.
- **The store sleeps.** The fleet's activator stops idle modules, so a refused
  connection to `:50152` is usually "asleep", not "broken": the store client asks
  the activator to wake it (`ENCRYPT_ACTIVATOR_URL`) and retries once. With no
  activator to ask, the failure is reported as-is.

## Tests

```bash
pytest mod/orbit/encrypt/test -q     # 19 tests
```

Sandbox and vault tests always run (no network, no store). The end-to-end pass —
bring a circuit → encrypt → store → open → download → delete — is skipped unless
the API and the store are up.

## Layout

```
config.json           ports, sandbox limits, endpoint docs
mod.py                the CLI surface (m encrypt/…) + serve/register
api/api.py            FastAPI gateway :50380
app/                  zero-dep console :50381 (node http, one index.html)
circuits/             reference circuits — examples, not installed
encryptor/
  engine.py           circuits + store + vault, the module's logic
  sandbox.py          how a circuit is run, and what is enforced
  runner.py           the isolated child process — the only place user code runs
  storeclient.py      the store mod, over its protocol auth
  vault.py            the metadata index (no secrets)
test/                 pytest
```
