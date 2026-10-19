# Simulation, examples and demo data

**`simulate.py`** plays the real protocol for years in a throwaway `Mod` with its own clock and an in-memory store: owner claims the seat, renters pay by sandbox bank transfer, reconcile books it, quarters close, the ledger refuses a payment past the home price. Numbers come from the same functions the API uses. `simulate_presets` lists scenarios; `simulate` runs one (`GET/POST /simulate`, MCP `openhouse_simulate`).

**`examples.py`** holds replayable stories told in real MCP tool calls, run in a temp store; each returns `steps` (every call, verbatim) and `checks` (what it proves): `first_rent`, `zero_fee`, `lease_option`, `payoff`, `bank_transfer`, `bank_proof`, `bank_statement`, `city_pause`, `bank_over_mcp`. Add one with the `@example(name, title, shows, level)` decorator.

**`demo.py`** `seed(force=)` fills the live store with a labelled fictional property ("DEMO - 128 Maple Ave", 60 days of backdated rent, 3 shareholders, 1 dividend) through the real code paths; `unseed` removes exactly what it wrote and refuses a store it did not seed. Marker file: `~/.openhouse/demo.json`.
