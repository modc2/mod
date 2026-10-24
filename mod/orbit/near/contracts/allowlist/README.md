# allowlist

The owner publishes one 32-byte root for a list of thousands of accounts

Made in the NEAR workshop (orbit/near) from the `merkle_allowlist` starter.

    cargo build --target wasm32-unknown-unknown --release   # -> allowlist.wasm
    cargo test                                             # unit tests, no chain

Deploy with the workshop (BUILD tab, "Ship") or by hand:

    m near/ship allowlist account_id=you.testnet
