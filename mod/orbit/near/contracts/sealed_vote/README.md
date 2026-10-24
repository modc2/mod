# sealed_vote

Everyone submits a sealed number, nobody can read it, nobody can copy it (the seal is bound to the sender), then everyone opens theirs and the contract checks each seal

Made in the NEAR workshop (orbit/near) from the `commit_reveal` starter.

    cargo build --target wasm32-unknown-unknown --release   # -> sealed_vote.wasm
    cargo test                                             # unit tests, no chain

Deploy with the workshop (BUILD tab, "Ship") or by hand:

    m near/ship sealed_vote account_id=you.testnet
