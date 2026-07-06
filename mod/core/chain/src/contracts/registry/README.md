# Registry

Minimal on-chain registry for mods. Each mod has an owner, a unique name (per creator), and arbitrary data (JSON/IPFS hash).

## Features

- Register mods with unique name + data
- Update data (owner only)
- Remove mods (frees name for reuse)
- Transfer ownership (enforces name uniqueness for new owner)
- User mod list tracking

## Interface

| Function | Description |
|---|---|
| `registerMod(name, data)` | Register a new mod |
| `updateMod(modId, data)` | Update mod data (owner) |
| `removeMod(modId)` | Delete a mod (owner) |
| `transferOwnership(modId, newOwner)` | Transfer mod ownership |
| `getMod(id)` | Get mod details |
| `getUserMods(user)` | List user's mod IDs |
| `isNameTaken(creator, name)` | Check name availability |

## Test

```sh
npx hardhat test src/contracts/registry/test/
```
