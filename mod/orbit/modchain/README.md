# modchain

Slim chain module: a single on-chain registry that decides **who owns each name in the proxy router**, ranked by **staketime**.

Same flavour as `chain`, but trimmed to one job — namespace priority. If two mods want `polymarket`, the one with more STT staked wins, and the router (`routy`) reflects that.

## How it works

1. A mod owner stakes STT on a name in `NamespaceRegistry`:
   `claim("polymarket", 10 STT, "http://localhost:3000", "app")`
2. Anyone can **outbid** by staking strictly more STT plus their own target URL. The previous owner's STT is queued for withdrawal — nothing is slashed.
3. A holder can `topUp` (raises the bar) or `release` (gets stake back, frees the name).
4. `modchain sync` reads `getActiveClaims()` from the chain and POSTs the resulting app/api list to `routy`'s `/_api/sync`. The router's namespace becomes whatever the chain says.

The contract uses the same STT token deployed by the `staketime` module — `scripts/deploy.js` reads `staketime/config.json` to find it.

## Layout

```
modchain/
├── src/
│   ├── contracts/
│   │   ├── NamespaceRegistry.sol   # claim / outbid / topUp / release
│   │   └── MockSTT.sol             # test-only ERC20
│   └── mod.py                      # python CLI + router sync
├── scripts/deploy.js
├── test/NamespaceRegistry.test.js
├── hardhat.config.js
├── package.json
└── config.json
```

## Usage

```bash
# Deploy (needs STT from staketime, or set STT_TOKEN env)
m modchain deploy
m modchain status

# Claim and outbid
m modchain claim       name=polymarket stake=10 target_url=http://localhost:3000 kind=app
m modchain claim       name=polymarket stake=20 target_url=http://other:3000     kind=app  # outbid
m modchain topup       name=polymarket amount=50
m modchain set_target  name=polymarket target_url=http://localhost:3001 kind=app
m modchain release     name=polymarket
m modchain withdraw

# Views
m modchain claims
m modchain get         name=polymarket
m modchain threshold   name=polymarket    # STT needed to outbid

# Push the chain's namespace to the local proxy router
m modchain sync
```

## Tests

```bash
cd /root/mod/mod/orbit/modchain
npm install
npx hardhat test
```
