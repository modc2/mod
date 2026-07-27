# sshville

Multi-host SSH connection manager, gated by either a MetaMask (Ethereum) or
SubWallet / Polkadot.js (sr25519, e.g. Bittensor) wallet signature. Each
wallet identity owns a **key vault** — multiple named credentials (passwords
or private keys) stored locally in `~/.sshville/keys.json`, encrypted
client-side with AES-GCM under a key derived from a **vault passphrase that
never leaves the browser** — plus a set of SSH targets that reference those
keys. The server only ever stores ciphertext and cannot reconstruct the
encryption key from anything it sees.

## Capabilities

- **Dual-curve wallet auth** — `X-Mod-Auth` carries either
  - `eth <0x-prefixed 65-byte personal_sign>` (secp256k1 / EIP-191), or
  - `sub <hex pubkey 32B> <hex sig 64B> [ss58_prefix=42]` (sr25519)
- **Bittensor-ready** — SS58 prefix defaults to 42, the generic Substrate
  prefix used by Bittensor; pass any other prefix for Polkadot (0), Kusama
  (2), Astar (5), etc.
- **Rust backend** — axum + tokio; `schnorrkel` for sr25519 verification,
  `k256` + `sha3` for ECDSA recovery, no FastAPI / Python in the hot path
- **Key vault (client-only secret)** — named, reusable keys encrypted under
  `PBKDF2(vault passphrase, random salt, 310k iters)` → AES-GCM-256. The
  passphrase is never transmitted; a per-vault verifier ciphertext lets the
  client detect a wrong passphrase without touching real keys. Passphrase
  rotation decrypts + re-encrypts every key in the browser and swaps the set
  atomically via `POST /vault`
- **Backend keygen** — `POST /keys/generate` mints an ed25519 keypair with
  the `ssh-key` crate **in memory only**; the browser encrypts the private
  key under the vault passphrase before storing it, so the zero-knowledge
  property holds. The OpenSSH public key + SHA256 fingerprint are stored in
  plaintext alongside the ciphertext for sharing
- **QR sharing** — `POST /qr` renders any text (public key to hand to
  another admin, or the locally-decrypted secret for a device-to-device
  move) as an SVG QR; POSTed so key material never lands in access logs
- **Legacy inline secrets** — pre-vault connections carry their own
  ciphertext under `PBKDF2(signature, salt = wallet_id, 100k iters)`; still
  supported, but weaker (the signature also travels in the auth header)
- **Per-wallet isolation** — connection records are bucketed by recovered
  identity (`eth:0x…` or `sub:5…`); namespaces don't collide
- **Test + exec** — server shells out to OpenSSH (`sshpass` for password
  auth, ephemeral `-i $TMPKEY` for key auth); the freshly-decrypted secret
  is sent once per call and never persisted

## Usage

### Python / CLI

```python
import mod as m
ssh = m.mod('sshville')()

ssh.info()
ssh.build(release=True)              # cargo build the rust api once
ssh.serve()                          # api :50182 + app :50183
ssh.kill()

# store ops (work directly on ~/.sshville/connections.json)
ssh.list(wallet='eth:0xabc…')        # → {id: conn}
ssh.list(wallet='sub:5GrwvaEF…')
ssh.add(wallet='eth:0xabc…', name='prod', host='1.2.3.4', user='root',
        ciphertext='…', iv='…', auth_type='password')
ssh.test(wallet='eth:0xabc…', id='root@1.2.3.4:22', secret='pw')
ssh.exec(wallet='eth:0xabc…', id='root@1.2.3.4:22',
         secret='pw', command='df -h')
ssh.remove(wallet='eth:0xabc…', id='root@1.2.3.4:22')
```

```bash
m sshville                                              # info (default)
m sshville/build
m sshville/serve
m sshville/kill
m sshville/list wallet=eth:0xabc...
m sshville/add wallet=eth:0xabc... name=prod host=1.2.3.4 user=root \
              ciphertext=… iv=… auth_type=password
m sshville/test wallet=eth:0xabc... id=root@1.2.3.4:22 secret=...
m sshville/exec wallet=eth:0xabc... id=root@1.2.3.4:22 \
                secret=... command="df -h"
```

## API Endpoints

| Method | Path                        | Auth | Description                                |
|--------|-----------------------------|------|--------------------------------------------|
| GET    | /health                     | no   | Health check                               |
| GET    | /info                       | no   | Module info + store stats                  |
| GET    | /challenge                  | no   | Challenge text + accepted schemes + format |
| GET    | /connections                | yes  | List the wallet's connections (no secrets) |
| GET    | /connections/{id}           | yes  | Fetch one connection (with ciphertext)     |
| POST   | /connections/add            | yes  | Upsert a connection (inline ciphertext+iv, or key_id → vault key) |
| DELETE | /connections/{id}           | yes  | Remove a connection                        |
| POST   | /connections/{id}/test      | yes  | Open transient SSH, run `whoami && uname`  |
| POST   | /connections/{id}/exec      | yes  | Run a one-shot command over SSH            |
| GET    | /vault                      | yes  | Vault KDF params (salt, verifier, iters)   |
| POST   | /vault                      | yes  | Create the vault, or rotate the passphrase (body carries the re-encrypted key set) |
| GET    | /keys                       | yes  | List vault keys (metadata only) + vault    |
| POST   | /keys/add                   | yes  | Upsert a named key (ciphertext + iv; optional plaintext public_key + fingerprint) |
| POST   | /keys/generate              | yes  | Mint an ed25519 keypair in memory, return it once — never persisted server-side |
| GET    | /keys/{id}                  | yes  | Fetch one key with ciphertext              |
| DELETE | /keys/{id}                  | yes  | Delete a key (refused while referenced)    |
| POST   | /qr                         | yes  | Render `{data}` as a QR SVG (POST so nothing hits URL logs; stateless) |

All authed endpoints accept the `X-Mod-Auth` header:

```
X-Mod-Auth: eth <0x-prefixed signature>                        # MetaMask
X-Mod-Auth: sub <pubkey-hex> <sig-hex> [ss58_prefix=42]        # SubWallet etc.
```

The `sub` scheme verifies the sr25519 signature against both
`<Bytes>CHALLENGE</Bytes>` (what Polkadot.js / SubWallet sign by default for
`signRaw{type:'bytes'}`) and the bare challenge, so headless signers also
work.

## Structure

```
sshville/
├── mod.py                            # Mod anchor — lifecycle, CLI helpers
├── config.json                       # ports, fns, endpoints
├── skill.md
└── src/
    ├── api/                          # Rust backend (axum)
    │   ├── Cargo.toml
    │   └── src/
    │       ├── main.rs               # bootstrap + cors
    │       ├── lib.rs                # AppState
    │       ├── auth.rs               # eth + sub signature recovery
    │       ├── store.rs              # JSON store, file-locked persist
    │       ├── ssh.rs                # OpenSSH subprocess runner
    │       └── routes.rs             # axum handlers
    └── app/index.html                # SPA: MetaMask + SubWallet, AES-GCM
```

## Environment

- `SSHVILLE_PORT` — override the API port (default `50182`)
- `SSHVILLE_STORE_DIR` — override the connection store dir
  (default `~/.sshville/`, file `connections.json`)
- `SSHVILLE_CHALLENGE` — override the signed challenge string
  (default `sshville-auth-v1`)
- Host needs `cargo` (for `build`/`serve` first run) and `ssh` on PATH;
  for password auth, `sshpass` too (`brew install hudochenkov/sshpass/sshpass`)

## Mod Protocol

- name: `sshville`
- api port: `50182`
- app port: `50183`
- backend: Rust (axum + schnorrkel + k256)
- challenge: `sshville-auth-v1`
- store: `~/.sshville/connections.json` — `{wallet_id: {conn_id: connection}}`
- keys:  `~/.sshville/keys.json` — `{wallet_id: {vault: {salt, verifier_ct,
  verifier_iv, kdf_iters}, keys: {key_id: {name, kind, ciphertext, iv}}}}`;
  ciphertext is AES-GCM under the client-only vault passphrase key
  where `wallet_id` is `eth:0x…` or `sub:5…`
