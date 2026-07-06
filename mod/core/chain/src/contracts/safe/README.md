# Safe

Multisignature wallet with EIP-712 threshold signatures, proxy pattern, and factory deployment.

## Contracts

| File | Description |
|---|---|
| `Safe.sol` | Core multisig logic — owner management (linked list), threshold voting, EIP-712 tx signing, nonce replay protection |
| `SafeProxy.sol` | Minimal proxy delegating to Safe singleton |
| `SafeProxyFactory.sol` | Factory for deploying Safe proxies with `create2` |
| `ISafe.sol` | Interface |
| `Enum.sol` | Operation enums (Call / DelegateCall) |

## Features

- Add/remove owners with threshold management
- Execute transactions with collected EIP-712 signatures
- Proxy pattern for gas-efficient deployment
- Nonce-based replay protection

## Test

```sh
npx hardhat test src/contracts/safe/test/
```
