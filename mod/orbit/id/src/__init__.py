"""id — one identity, many accounts.

    crypto/     the primitives, so a proof needs nothing installed to re-check
    chains.py   what each chain calls an address, and what a wallet signs
    accounts.py the accounts that cannot sign, and how they prove themselves
    statement.py the exact words that get signed
    store.py    the append-only log the identity actually is
    identity.py the rules: who may join, who may leave, how two become one
"""
