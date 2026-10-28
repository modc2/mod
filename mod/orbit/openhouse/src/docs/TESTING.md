# Testing

```bash
cd src && python3 -m pytest tests -q                 # 149 tests, ~80 s
cd src/contracts && /root/.foundry/bin/forge test    # Solidity
```

| File | Covers |
|---|---|
| `test_mcp.py` | MCP router, tool list, writes, errors |
| `test_bank.py` | adapters, statement formats, reconciler |
| `test_proofs.py`, `test_proofs_chain.py` | issue/verify/open, Ethereum verifier end to end |
| `test_pq.py` | OHX-KECCAK and ML-DSA, vectors |
| `test_civic.py` | civic server and overrides |
| `test_simulate.py`, `test_examples.py`, `test_demo.py` | simulator, walkthroughs, seed |
| `test_fx.py` | currency rail (offline, stubbed) |
| `test_local_chain.py` | anvil deploy via `localchain.sh` |

Tests use a temp `HOME` so they never touch `~/.openhouse`. Test files compute `MODULE_DIR` as `src/` and put `/root/mod` on `sys.path`.
