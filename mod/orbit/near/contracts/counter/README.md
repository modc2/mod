# counter

The smallest real contract: one number in storage, a view that reads it, two changes that move it, an owner-only reset, and a log line per change

Made in the NEAR workshop (orbit/near) from the `counter` starter.

    cargo build --target wasm32-unknown-unknown --release   # -> counter.wasm
    cargo test                                             # unit tests, no chain

Deploy with the workshop (BUILD tab, "Ship") or by hand:

    m near/ship counter account_id=you.testnet
