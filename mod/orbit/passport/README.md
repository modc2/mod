# passport

The fleet's one auth protocol. Sign in once (wallet, local key, or the build
console) and every module recognises you.

Modules don't carry their own verifier. They ask passport who a token belongs
to. The rules live in two files that are re-read whenever they change:

| file | what it decides |
|---|---|
| `protocol.json` | which schemes are accepted, max ages, cache TTL, browser slot / message names |
| `src/schemes.py` | how each scheme is verified (`detect` + `verify_<scheme>`) |

Edit either one and the next request anywhere in the fleet follows the new
rules: the service, every Python client, and build's Rust API (through its
fallback). Nothing restarts. If an edit breaks `schemes.py`, the last good
verifier keeps serving.

## Schemes (v1)

- **mod**: the wallet/key-signed token, `base64url({data, time, key, signature})`.
  The **canonical shape** is `data` = a string scope, `time` = whole unix
  seconds, `signature` = EIP-191 `personal_sign` over
  `JSON.stringify({data, time})`. Python modules, build's Rust API and
  passport all accept this shape. The legacy dict-data / float-time shape is
  still accepted here. Non-EIP-191 signatures (server keys, sr25519) are
  delegated to the core `m.mod('auth')`.
- **build**: build console session bearer `address:ts[:ho]:hmac`. Only build
  holds the HMAC secret, so passport asks build (MCP `whoami`), which honours
  expiry, sign-out and guest grants.

## Service (:51390)

```
GET /verify?token=…           {address, scheme, time} | 401 {error}
GET /role?token=…&mod=agent   {signed_in, address, is_owner, owner}  (mod=build → build's full role)
GET /protocol                 the live protocol + fingerprint
GET /passport.js              browser SDK
```

The token can also arrive as `Authorization: Bearer …` or a `token:` header.

## Adopting it

**Python module** (two lines, no restart needed when the protocol changes):

```python
import importlib.util
spec = importlib.util.spec_from_file_location('passport_client', '<orbit>/passport/src/client.py')
pp = importlib.util.module_from_spec(spec); spec.loader.exec_module(pp)

pp.address(token)          # '0x…' or None
pp.role(token, mod='mymod')  # {'is_owner': …}
```

The client asks the service. If the service is down, it verifies in-process
with the same hot-reloading `Passport`. See `orbit/agent/src/identity.py:passport()`.

**Browser console:**

```html
<script src="/passport/passport.js"></script>
<script>
  const s = await Passport.ready()   // shared slot → build session → parent frame
  if (!s) await Passport.signIn('mymod')
  fetch('/api/mymod/x', { headers: { Authorization: `Bearer ${Passport.session().token}` } })
</script>
```

**Rust:** see `orbit/build/src/api/src/auth.rs:passport_verify`. Verify
natively when you can and fall back to `GET :51390/verify`. Never forward
your own session shape back to passport, or the two will loop.

## Wired so far

- **agent**: `_resolve_address` → passport. Build sessions sign you in, and
  the build owner gets owner rights and harnesses. The console mints canonical
  tokens, mirrors them to `mod_passport`, and adopts `mod_passport` /
  `build_jobs_token` / the parent's `mod.session` on load.
- **build**: accepts the canonical token natively (`token:` header,
  `/auth/core`). Any other token in either slot goes to passport (needs a
  build-api restart to take effect).

## Migrating the rest of the fleet

About 40 of the ~350 orbit modules call `m.mod('auth')` directly. Four still
use the incompatible `auth.base` (quests, logo, crates, coplay). Order:

1. Modules that already route through one helper (`_resolve_address`,
   `identity.py`, `auth.py`): swap the helper body for `pp.address()`.
   That's one edit per module.
2. `auth.base` users. They currently reject every standard token.
3. Consoles with their own sign-in: load `passport.js`, sign in with
   `Passport.signIn(scope)`, and drop the per-module localStorage slot.
4. Owner checks: `pp.role(token, mod)` reads the off-tree
   `~/.mod/<mod>/owner.json` (owner + co_owners), falling back to config.json.
