# registry

Multi-chain module registry supporting EVM (Base), Solana, NEAR, and off-chain backends. Register, query, update, and manage modules across chains with IPFS/Lighthouse/Filecoin storage.

## Structure

```
registry/
├── registry/              # Core module
│   ├── mod.py             # Unified Mod interface
│   ├── base.py            # Abstract backend interface
│   ├── config.json        # Network configs + deployed addresses
│   ├── deploy.py          # Multi-chain deployer
│   └── offchain/          # Local JSON backend
├── onchain/               # On-chain backends
│   └── src/
│       ├── evm/           # Base/EVM (Registry.sol + web3.py)
│       ├── solana/        # Solana (memo tx + Anchor program)
│       └── near/          # NEAR (near-sdk Rust contract)
├── tests/                 # Test suite
└── app/                   # Next.js frontend
```

## Usage

```python
import mod as m

# Default (offchain)
reg = m.mod('registry')()
reg.register('mymod', {'version': '1.0'})

# EVM backend
reg = m.mod('registry')(backend='evm', network='testnet')
reg.register('mymod', {'version': '1.0'}, storage='ipfs')

# List all mods
reg.list_all()

# Cross-backend sync
reg.sync(source='offchain', target='evm')
```

```bash
# CLI
m registry register name=mymod data='{"version": "1.0"}'
m registry list_all
m registry list_all backend=evm
```

## Backends

| Backend | Description | Storage |
|---------|------------|---------|
| `offchain` | Local JSON file (default) | `~/.mod/registry/` |
| `evm` | Registry.sol on Base Sepolia | On-chain + IPFS |
| `solana` | Memo transactions + local index | On-chain + local |
| `near` | NEAR contract via near-cli-rs | On-chain + local |

## Deploy

```bash
python registry/deploy.py                  # All networks (testnet)
python registry/deploy.py --network evm    # EVM only
python registry/deploy.py --network near   # NEAR only
python registry/deploy.py --network solana # Solana only
```

## Git URL & CID Registration

The core API registry (`mod/core/api`) supports registering modules from git URLs and IPFS CIDs. The `reg()` method auto-dispatches:

```python
api = m.mod('api')()

# Git URL (clones into portal, registers)
api.reg('https://github.com/user/repo', key='main')

# GitHub shorthand (user/repo auto-expands)
api.reg('user/repo', key='main')

# IPFS CID (resolves config, clones if git url present)
api.reg('QmXyz...', key='main', name='mymod')
```

```bash
m api/reg mod=https://github.com/user/repo key=main
m api/reg mod=user/repo key=main
m api/reg mod=QmXyz... key=main name=mymod
```

## API

| Method | Description |
|--------|-------------|
| `register(name, data)` | Register a new mod |
| `update(mod_id, data)` | Update mod data |
| `remove(mod_id)` | Remove a mod |
| `get(mod_id)` | Get mod by ID |
| `list(owner)` | List mods for owner |
| `list_all()` | List all mods |
| `transfer(mod_id, new_owner)` | Transfer ownership |
| `sync(source, target)` | Sync between backends |
| `resolve(data_uri)` | Resolve IPFS/Lighthouse CID to JSON |
