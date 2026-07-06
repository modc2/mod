# Perms

Hierarchical key-value permission system. Parent keys map to arrays of child keys, with first-setter ownership.

## Features

- Add/remove/set child keys under a parent key
- First caller to add becomes parent key owner
- Transfer key ownership
- Configurable limits: `maxChildKeys` (default 100), `maxKeySize` (default 1024 bytes)
- `setOwnerless()` for permanent decentralization

## Interface

| Function | Description |
|---|---|
| `addKey(parentKey, childKey)` | Add a child key |
| `removeKey(parentKey, childKey)` | Remove by value |
| `removeKeyAtIndex(parentKey, index)` | Remove by index |
| `setKeys(parentKey, childKeys[])` | Replace all child keys |
| `getKeys(parentKey)` | Get all child keys |
| `getKeyCount(parentKey)` | Count child keys |
| `transferKeyOwnership(parentKey, newOwner)` | Transfer ownership |

## Test

```sh
npx hardhat test src/contracts/perms/test/
```
