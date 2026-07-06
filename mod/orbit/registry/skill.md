# Registry

Multi-chain module registry with git URL, CID, and local module registration. Supports EVM (Base Sepolia), Solana, NEAR, and offchain backends with IPFS/Lighthouse/Filecoin storage.

## Capabilities

- **Register** -- register modules from local paths, git URLs, GitHub shorthand, or IPFS CIDs
- **Multi-chain** -- offchain (default), EVM, Solana, NEAR backends
- **Storage** -- IPFS, Lighthouse, Filecoin content-addressed storage
- **Versioning** -- version history, rollback via `setback()`
- **Forking** -- copy and re-register under a new key
- **Sync** -- cross-backend sync (e.g. offchain -> EVM)

## Usage

```python
import mod as m

# Core API registry (handles git/cid/local dispatch)
api = m.mod('api')()

# Register a local module
api.reg('mymod', key='main', comment='Initial release')

# Register from a git URL
api.reg('https://github.com/user/repo', key='main')

# Register from GitHub shorthand (user/repo)
api.reg('user/repo', key='main')

# Register from an IPFS CID
api.reg('QmXyz...', key='main', name='mymod')

# Direct git registration
api.reg_git('https://github.com/user/repo', name='mymod', key='main')

# Direct CID registration
api.reg_cid('QmXyz...', key='main', name='mymod')

# Fork a module
api.fork('modulename', key='main')

# Bulk register all local modules
api.regall(key='main')
```

```bash
# CLI
m api/reg mod=mymod key=main
m api/reg mod=https://github.com/user/repo key=main
m api/reg mod=user/repo key=main
m api/reg mod=QmXyz... key=main name=mymod
m api/fork mod=mymod key=main
m api/regall key=main
```

## Multi-chain registry (orbit module)

```python
reg = m.mod('registry')()

# Offchain (default)
reg.register('mymod', {'version': '1.0'})

# EVM backend
reg = m.mod('registry')(backend='evm', network='testnet')
reg.register('mymod', {'version': '1.0'}, storage='ipfs')

# List and sync
reg.list_all()
reg.sync(source='offchain', target='evm')
```

```bash
m registry register name=mymod data='{"version": "1.0"}'
m registry list_all
m registry list_all backend=evm
```

## API

| Method | Description |
|--------|-------------|
| `reg(mod)` | Auto-dispatch: local name, git URL, or CID |
| `reg_git(url, name, key)` | Clone from git URL and register |
| `reg_cid(cid, key, name)` | Resolve CID and register |
| `fork(mod, key)` | Copy module to portal and register |
| `regall(key, depth)` | Bulk register all local modules |
| `register(name, data)` | Register on multi-chain backend |
| `update(mod_id, data)` | Update mod data |
| `remove(mod_id)` | Remove a mod |
| `get(mod_id)` | Get mod by ID |
| `list(owner)` | List mods for owner |
| `list_all()` | List all mods |
| `versions(mod)` | Version history |
| `setback(mod, cid)` | Rollback to previous version |
| `sync(source, target)` | Sync between backends |

## Git URL Formats

| Format | Example | Result |
|--------|---------|--------|
| Full GitHub URL | `https://github.com/user/repo` | Clones directly |
| GitHub shorthand | `user/repo` | Expands to `https://github.com/user/repo` |
| GitLab URL | `https://gitlab.com/user/repo` | Clones directly |
| With `.git` suffix | `https://github.com/user/repo.git` | Stripped automatically |

## CID Formats

| Format | Description |
|--------|-------------|
| Config with `url` | `{name, url: "https://github.com/..."}` -- clones from git |
| Config without URL | `{name, description, ...}` -- creates local module |
| Legacy mod_info | `{name, content, schema}` -- writes files directly |

## Structure

```
registry/
  registry/
    mod.py            # Multi-chain registry interface
    base.py           # Abstract backend
    config.json       # Network configs + addresses
    deploy.py         # Multi-chain deployer
    offchain/         # Local JSON backend
  onchain/src/
    evm/              # Base/EVM (Registry.sol)
    solana/           # Solana (memo + Anchor)
    near/             # NEAR (near-sdk Rust)
```

## Mod Protocol

- Module: `registry`
- Config: `mod/orbit/registry/registry/config.json`
- Default backend: `offchain`
- EVM contract: `0x4f9e72C935e5762E941F98DA50696cb022008a43` (Base Sepolia)
