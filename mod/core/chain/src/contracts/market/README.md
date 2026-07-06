# Market

Stable token market with credit/debit/mint/withdraw operations. 8-decimal ERC20 credit token backed by oracle-priced payment tokens.

## Contracts

| File | Description |
|---|---|
| `Market.sol` | Core market — credit (deposit tokens, receive stable), withdraw (burn stable, receive tokens), mint with ERC20 or ETH, pause, slippage protection |
| `debit/Debit.sol` | EIP-712 signature-based debit system — authority approvals, daily spending limits, multisig threshold, nonce replay protection |

## Features

- **Credit**: deposit payment tokens, receive stable tokens (configurable fee)
- **Withdraw**: burn stable tokens, receive payment tokens at oracle price
- **Mint**: pay with whitelisted tokens or ETH, mint stable 1:1 USD via oracle
- **Debit**: authority-approved spending with EIP-712 sigs, daily limits, multisig
- 5% treasury fee on debits
- Slippage protection (`maxPaymentAmount`, `minReceiveAmount`)
- Pausable by owner
- Oracle price conversion via TokenGate

## Test

```sh
npx hardhat test src/contracts/market/test/
```
