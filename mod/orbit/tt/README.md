# staketime

Delegated staking + Yuma Consensus emission distribution on EVM (Base Sepolia).

## Structure

```
staketime/
├── src/
│   ├── contracts/         # Solidity (StakeTime, Subnet, Registry, NativeToken)
│   ├── mod.py             # Python CLI
│   ├── api/api.py         # FastAPI backend
│   └── app/               # Next.js dashboard
├── scripts/deploy.js
├── test/StakeTime.test.js
└── hardhat.config.js
```

## Usage

```bash
m staketime status
m staketime deploy
m staketime serve
m staketime register key=val1 key_type=1 commission_bps=1000
m staketime stake_on validator_key=val1 amount=1000 lock_blocks=43200
```
