# Civic layer

A government can seat itself on a property and supervise it.

- **On-chain**: `CivicRegistry.sol` is a permissionless directory (key, jurisdiction, server URL). Identity is proven by the government publishing its key on its own `.gov` domain.
- **Module**: `civic` (state), `civic_charter`, `civic_override` (pause / unpause / hold / release), `civic_resign`. A paused property refuses payments; the `city_pause` example walks through it.
- **Server** (`civic/server.py`, FastAPI): run by the government on its own box and key. `VERIFY` pulls a property's terms and ledger and recomputes every split (fee, equity, owner income, the price clamp, the 0-5% band), reporting any difference entry by entry. `OVERRIDE` signs and submits the pause or hold transaction with the government's key (kept in `~/.mod/openhouse/`, hardware, or a Safe).

Tests: `tests/test_civic.py`, `contracts/test/Civic.t.sol`.
