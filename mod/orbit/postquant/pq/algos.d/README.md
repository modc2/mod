# algos.d — key-type plugins

Every `.py` file here (and in `~/.mod/postquant/algos/`) is loaded when the
node starts and may register signature algorithms — new key types for
wallets, witnesses and block seals. No pip, no build step: one file, one
`register` function.

## The contract

```python
def keygen(seed: bytes) -> (pk: bytes, sk: bytes)   # deterministic, 32-byte seed
def sign(sk, msg: bytes, ctx: bytes = b"") -> bytes
def verify(pk, msg, sig, ctx: bytes = b"") -> bool  # False, never raise

def register(algos):
    algos.register(algos.SigAlgo(
        "my-algo-name", keygen, sign, verify,
        sizes={"pk": ..., "sig": ..., "seed": 32},
        family="...", standard="...",
        basis="what the security rests on",
        quantum_safe=True))       # be honest — False means the chain
                                  # lists you but refuses your witnesses
```

Rules the chain holds you to:

- **Deterministic keygen.** Wallets store the 32-byte seed, nothing else.
  The same seed must always produce the same keypair.
- **ctx is load-bearing.** Signatures are minted with a context string
  (`postquant/tx/v1`, `postquant/block/v1`); a signature for one context must
  not verify under another. Wrap it however your scheme likes — the built-ins
  use the FIPS length-prefix framing.
- **Names are permanent.** The algorithm name is hashed into every address
  created under it. Rename it and those addresses are unreachable.
- **quantum_safe is a claim you make in public.** It shows in the catalog
  next to your name. Witnesses from `quantum_safe=False` algorithms are
  refused unless the operator sets `POSTQUANT_ALLOW_CLASSICAL=1`.
- **Bytes are billed.** Witness gas is charged on your actual signature and
  public key bytes. A 17KB signature is allowed; it just pays.

A broken plugin is skipped and reported under `plugin_errors` in
`pq_algos` / `GET /algos` — it cannot take the node down.

`ed25519.py` in this directory is a complete worked example (and a
deliberately gated one — it declares `quantum_safe=False`).
