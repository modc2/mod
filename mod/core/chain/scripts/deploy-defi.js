/**
 * Deploy the DeFi yield-aggregator category (YieldVault + mock strategies) and
 * wire its addresses into config.json.
 *
 *   npx hardhat run scripts/deploy-defi.js --network localhost
 *   CONFIG_NET=testnet npx hardhat run scripts/deploy-defi.js --network base_sepolia
 *
 * Requires the core contracts (Market, NativeToken/Market token, USDC, USDT, TokenGate)
 * to already be deployed in config.json for the target network. The Market contract IS
 * the native reward ERC20, so the vault's reward token == the Market address.
 *
 * The mock strategies give working lowfi yield options on localhost/testnet without an
 * external protocol. Wire a real AaveV3Adapter on mainnet by passing the Aave Pool /
 * aToken addresses (left as a documented manual step — see src/contracts/defi/README.md).
 */
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const NET_MAP = { base_sepolia: "testnet", base: "mainnet", hardhat: "localhost" };

async function main() {
  const ethers = hre.ethers;
  const cfgPath = path.join(__dirname, "..", "config.json");
  const cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));

  const netKey = process.env.CONFIG_NET || NET_MAP[hre.network.name] || hre.network.name;
  const dep = cfg.deployments[netKey];
  if (!dep) throw new Error(`No deployments for network '${netKey}' in config.json`);
  const contracts = dep.contracts;
  const market = contracts.Market && contracts.Market.address;
  if (!market) throw new Error(`Market not deployed for '${netKey}'`);

  console.log(`Deploying defi on '${netKey}' (market/native = ${market})`);

  // Vault: reward token is the Market token itself.
  const Vault = await ethers.getContractFactory("YieldVault");
  const vault = await Vault.deploy(market, market);
  await vault.waitForDeployment();
  const vaultAddr = await vault.getAddress();
  console.log(`  YieldVault -> ${vaultAddr}`);

  contracts.YieldVault = { address: vaultAddr, contract: "YieldVault" };

  // One mock lowfi-yield strategy per stable asset present in config.
  const Mock = await ethers.getContractFactory("MockYieldAdapter");
  for (const sym of ["USDC", "USDT"]) {
    const asset = contracts[sym] && contracts[sym].address;
    if (!asset) continue;
    const adapter = await Mock.deploy(asset, vaultAddr, `${sym} · Conservative`);
    await adapter.waitForDeployment();
    const aAddr = await adapter.getAddress();
    const tx = await vault.addStrategy(asset, aAddr, `${sym} · Conservative`);
    await tx.wait();
    contracts[`MockYieldAdapter_${sym}`] = { address: aAddr, contract: "MockYieldAdapter" };
    console.log(`  MockYieldAdapter(${sym}) -> ${aAddr} (strategy registered)`);
  }

  fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 4) + "\n");
  console.log("config.json updated.");
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
