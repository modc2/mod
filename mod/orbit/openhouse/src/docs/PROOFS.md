# Proof of rent paid, without the tenant

The bank sees a payment whole. The chain only needs to know that property P was paid for period T.

**Statement** (`proof/src/lib.cairo`, run with `scarb cairo-run`):
- private: tenant, salt, amount_paid
- public: `[version, property_id, period, rent_due, commitment, nullifier]`
- `commitment = Poseidon(tenant, salt)`; `nullifier = Poseidon(commitment, property_id, period)` so one proof books one month once. An under-payment has no proof.

**Fact**: `keccak256(program_hash || keccak256(outputs))`. Three verifiers compute the same fact:
- Ethereum: `contracts/OpenHouseProofs.sol` (against a STARK fact registry)
- NEAR: `near/src/lib.rs` (WASM, bank's ed25519 signature checked natively)
- Python: `proofs.py` (issue, verify, open with the salt, submit, chain status)

## Post-quantum layer (`pq.py`, `contracts/OHX.sol`, `near` `submit_pq`)

- **OHX-KECCAK**: stateful hash-based signature (WOTS+ under a two-layer Merkle hypertree, keccak only). Byte-for-byte twins in Python, Solidity and Rust; vectors in `tests/vectors/ohx.json`.
- **ML-DSA-65**: loaded from the sibling `postquant` module when present, else receipts carry OHX alone. Override the path with `OPENHOUSE_MLDSA`.
- `require_pq` is a one-way switch per verifier that closes the classical (ed25519/ECDSA) door. `proof_pq`, `proof_pq_upgrade`, `proof_pq_rotate` manage keys.
- OHX keys are stateful: never reuse a leaf; `rotate` moves to a fresh tree.

Build and deploy: `proof_build`, `proof_deploy_near`, `proof_chain`. NOTE: the NEAR contract deployed earlier has not been redeployed with the PQ door.
