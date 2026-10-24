"""zkstack — the zkapi module's driver for the upstream Ethereum zkAPI framework.

Everything cryptographic or protocol-bearing is upstream code from the pinned
vbuterin/zkapi checkout in ``vendor/zkapi``:

* ``ZkApiVault`` + ``Groth16ProofAdapter`` + ``Bn254Poseidon`` (Solidity)
* ``zkapi serverd`` (lease authorization, settlement, Schnorr state signing)
* ``zkapi-indexerd`` (vault event mirror, Merkle tree snapshots/paths)
* ``zkapi-challenged`` (stale-escape challenger)
* ``zkapi_browser`` wallet core + Groth16 provers (wrapped by ``src/wallet``)

This package only orchestrates them: a persistent local devnet, an
OpenRouter-compatible provider for lease keys, a keeper for the Chainlink-style
price feed, the client flows (deposit -> request -> settle -> withdraw), and
read-only verification of the live mainnet/Sepolia deployments.
"""
