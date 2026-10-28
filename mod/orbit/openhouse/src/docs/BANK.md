# Banks

`bank/core.py` defines one shape (`Account`, `Txn`, `Payment`; amounts signed from the holder's side, + is money in) and the **reconciler**: any transfer carrying a renter's reference code is booked onto the rent ledger exactly once.

| Adapter | File | Needs |
|---|---|---|
| `sandbox` | `sandbox.py` | Nothing. A complete simulated bank in one JSON file; IBANs under country code `XT`. `receive()` stands in for "a renter sent a transfer". |
| `statement` | `statements.py` | Nothing. Reads camt.053, MT940, OFX/QFX or any CSV; writes ISO 20022 pain.001. Samples in `bank/samples/`. |
| `berlin`, `obie` | `openbanking.py` | A bank API and credentials (PSD2 / UK OBIE read side). |
| `mcp` | `remote.py` | Any bank MCP server. Point it at the server and name the tools that list accounts, list transactions and pay. |
| reference bank | `mockbank.py` | A three-tool MCP server (`list_accounts`, transactions, pay) that doubles as a template for real banks. |

Flow: `bank_connect` -> `bank_reference(address)` gives a renter their code -> `bank_link` ties an account/reference to an address -> transfers arrive (`bank_receive`, `bank_import`, or a live API) -> `bank_reconcile` books them via `pay_rent`. Before passing a manual `rate=`, call `fx` to see the live ETH/fiat rate.

Everything except the sandbox is a real bank and needs the operator's key. Credentials live in `~/.mod/openhouse/`, not the repo.
