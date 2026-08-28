# encrypt — encrypted messages with bring-your-own-circuit cryptography

The module ships **no cipher**. The user uploads a *circuit* (a python file with
`encrypt`/`decrypt`), it runs server-side in a sandbox, and only the ciphertext
is kept — in the `store` mod, under the caller's own wallet identity, as a CID.
Keys are used for one request and never persisted.

**Ports:** API `50380`, console `50381` at `/encrypt`. Start with `m encrypt/serve`.
**Depends on:** `store` (`:50152`) — encrypt has no storage of its own.

## Quick reference

```sh
m encrypt/status                                 # sandbox + store + vault state
m encrypt/examples                               # reference circuits (not installed)
m encrypt/add_circuit circuits/chacha_poly.py    # bring a circuit (selftest + pin)
m encrypt/circuits                               # what I can encrypt with
m encrypt/encrypt "meet at nine" circuit=c8e1f… key=hunter2 label=note
m encrypt/messages                               # my ciphertext, by CID
m encrypt/open m63aff… key=hunter2               # decrypt server-side
m encrypt/download m63aff… out=./note.enc        # raw ciphertext
m encrypt/rm m63aff…                             # delete server-side
m encrypt/purge confirm=1                        # delete everything I own
```

## The circuit contract

```python
def encrypt(data: bytes, key: bytes, params: dict) -> bytes
def decrypt(data: bytes, key: bytes, params: dict) -> bytes
```

`key` is the raw passphrase — KDF is the circuit's job. Salt/nonce/tag go inside
the returned bytes; `params` is a public dict stored with the message and passed
back on decrypt. Uploads must pass a sandbox roundtrip (`decrypt(encrypt(x))==x`
**and** `encrypt(x) != x`), so a pass-through cipher is rejected.

## Things to know before changing this module

- **`encryptor/runner.py` is the only place user code runs.** It gets the source
  on *stdin*, never a path, so it can drop privileges before touching anything.
  Keep it dependency-free and importable-by-path-free — it is started with
  `python3 -I -B` and a scrubbed env.
- **Isolation degrades, and says so.** `unshare -n` (no network) and the drop to
  `nobody` need root; `sandbox.capabilities()` reports what is actually in force
  and `/status` shows it. Never widen a claim there without widening the check.
- **The caller's token is forwarded to the store**, never a module key. That is
  what makes the caller the object owner, keeps the store's quota/terms/ACL in
  play, and lets `DELETE /rm` work at all. Don't add a service account.
- **Nothing secret goes in `~/.mod/encrypt/`.** The vault holds CIDs, circuit ids
  and labels. If a field would help decrypt a message, it doesn't belong there.
- **Store delete is index-only today.** `dstore.rm()` drops the DB row but
  `localfs` keeps the bytes, so a held CID still resolves. `engine.delete()`
  verifies with a 1-byte `/preview` and reports the truth in `store_removed` —
  keep that check if you touch delete. A real fix belongs in `dstore.rm()`
  (call `localfs.rm(cid)`).
- **`m.mod('auth')().token(key=…)` signs with one key while declaring another.**
  Pass the key to the *constructor* (`m.mod('auth')(key=name)`), which is what
  `mod.py:token()` does.
- **Start the API from the repo root.** `python -m uvicorn` puts the cwd on
  `sys.path[0]`, and this module's own `mod.py` shadows the `mod` package if pm2
  starts it from the module directory (`serve_api` passes `cwd=repo_root`).
- **Circuit ids are content-addressed** (`c` + sha256[:16] of the source). Two
  uploads of the same file are one row — convenient, but it means a test that
  uploads a shipped circuit verbatim and deletes it afterwards would delete the
  user's copy. The suite appends a marker line (`fixture()`) to stay distinct.
- **A refused store connection means "asleep", not "broken".** The activator
  scale-to-zero proxy stops idle modules; `storeclient._wake()` POSTs to
  `/_activator/control` and retries once. Keep request bodies as bytes (not file
  handles) so that retry stays safe.
- **The console is zero-dep on purpose** — one `index.html` plus a node http
  server that proxies `${BASE}/_api/*` to the API, so the browser sees one origin
  both locally and behind the gateway at `/encrypt`. No npm install, no build.

## Tests

`pytest mod/orbit/encrypt/test -q` — 19 tests. Sandbox/vault tests always run;
the end-to-end HTTP pass skips unless the API and the store are up.
