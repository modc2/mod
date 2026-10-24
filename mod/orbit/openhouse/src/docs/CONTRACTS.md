# Contracts

Solidity 0.8.24, Foundry. Run tests with `/root/.foundry/bin/forge test` in `src/contracts/`.

| File | Role |
|---|---|
| `OpenHouse.sol` | The core: `payRent(token, amount)`, 90-day quarters, `closeQuarter`, `claim`, equity views. All accounting is 18-decimal USD value, so a 6-decimal USDC and an 18-decimal DAI dollar buy equal equity. |
| `ApprovedTokens.sol` | Deployer-owned payment-token registry: `enable / enableNative / disable / reprice`, native coin is `address(0)`. Never enable fee-on-transfer or rebasing tokens. |
| `OpenHouseTrust.sol` | One house, one mortgage, many owners. Shares are a receipt for dollars that reached the loan. The bank controls it but has no burn or seize function. |
| `OpenHouseFactory.sol` | A group posts a formation; only the named bank can `underwrite` it, which deploys the trust. |
| `MortgageOracle.sol` | `IMortgageOracle`: the only way servicer facts (balance, due, current) enter a trust. |
| `CivicRegistry.sol` | Permissionless directory of governments: key, jurisdiction, verification-server URL. Identity is proven off-chain via the government's own domain. |
| `OHX.sol` | On-chain verifier for the OHX-KECCAK post-quantum signature. |
| `OpenHouseProofs.sol` | Proof-of-rent verifier against a STARK fact registry; `MockFactRegistry.sol` stands in on test chains. |

## Governance (OpenHouse.sol)

Every lever (set terms, treasury, yield vault, transfer owner, set bank, sweep, unpause) goes `propose` by one seat, `approve` by the other, then executes. Seats are owner and bank and can never collapse into one key. Proposals expire after 7 days. Either seat may `pause()` alone (freezes `payRent`, `claim`, sweep); unpause needs both. A stolen owner key can veto forever but cannot move funds, so the bank seat should be a Safe.

Fee band: `MIN_FEE_BPS = 0`, max 5%. Do not reintroduce a 1% floor.

## Tests and local chain

`contracts/test/`: `Multisig`, `Tokens`, `Trust`, `Civic`, `PQ`, `Proofs`. `contracts/localchain.sh` starts its own anvil (port 8547), deploys a property and pays one month of rent; set `RPC_URL`, `OWNER_KEY`, `BANK_KEY`, `RENTER_KEY` to use another EVM chain (keys from env only).
`mod.compile()` shells out to `forge build`; `deploy` is the module's deploy entry.
