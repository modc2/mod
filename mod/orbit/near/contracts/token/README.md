# token

A fungible token written out in full, no library: balances in a LookupMap, owner-only mint, burn, and ft_transfer with the one-yoctoNEAR safety deposit every NEAR wallet expects

Made in the NEAR workshop (orbit/near) from the `token` starter.

    cargo build --target wasm32-unknown-unknown --release   # -> token.wasm
    cargo test                                             # unit tests, no chain

Deploy with the workshop (BUILD tab, "Ship") or by hand:

    m near/ship token account_id=you.testnet
