const hre = require("hardhat");
const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

/**
 * Deploy PreFi Trading Protocol
 * PreFiToken → PreFiVault → PreFiStaking + setup minters/withdrawers + initial markets
 */

const ADDRESSES = {
  base: {
    usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    swapRouter: "0x2626664c2603336E57B271c5C0b26F421741e481",
    weth: "0x4200000000000000000000000000000000000006",
  },
  baseSepolia: {
    usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    swapRouter: "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4",
    weth: "0x4200000000000000000000000000000000000006",
  },
};

// Default markets to add (token → feeTier)
const DEFAULT_MARKETS = {
  baseSepolia: [
    { name: "WETH", token: "0x4200000000000000000000000000000000000006", feeTier: 3000 },
  ],
  base: [
    { name: "WETH", token: "0x4200000000000000000000000000000000000006", feeTier: 500 },
    { name: "cbBTC", token: "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", feeTier: 3000 },
  ],
};

async function main() {
  const [deployer] = await ethers.getSigners();
  const network = hre.network.name;
  const balance = await ethers.provider.getBalance(deployer.address);

  console.log("\nPreFi Trading Protocol Deployment");
  console.log("=".repeat(50));
  console.log("Network:", network);
  console.log("Deployer:", deployer.address);
  console.log("Balance:", ethers.formatEther(balance), "ETH\n");

  const addrs = ADDRESSES[network];
  if (!addrs) throw new Error(`Network ${network} not supported`);

  const deployed = {};

  // 1. Deploy PreFiToken
  console.log("1. Deploying PreFiToken...");
  const PreFiToken = await ethers.getContractFactory("PreFiToken");
  const token = await PreFiToken.deploy();
  await token.waitForDeployment();
  deployed.prefiToken = await token.getAddress();
  console.log("   PreFiToken:", deployed.prefiToken);

  // 2. Deploy PreFiVault
  console.log("2. Deploying PreFiVault...");
  const PreFiVault = await ethers.getContractFactory("PreFiVault");
  const vault = await PreFiVault.deploy(addrs.usdc, deployed.prefiToken, addrs.swapRouter);
  await vault.waitForDeployment();
  deployed.prefiVault = await vault.getAddress();
  console.log("   PreFiVault:", deployed.prefiVault);

  // 3. Deploy PreFiStaking
  console.log("3. Deploying PreFiStaking...");
  const PreFiStaking = await ethers.getContractFactory("PreFiStaking");
  const staking = await PreFiStaking.deploy(deployed.prefiToken, addrs.usdc, deployed.prefiVault);
  await staking.waitForDeployment();
  deployed.prefiStaking = await staking.getAddress();
  console.log("   PreFiStaking:", deployed.prefiStaking);

  // 4. Setup: vault as minter on token
  console.log("4. Setting vault as PREFI minter...");
  const tx1 = await token.setMinter(deployed.prefiVault, true);
  await tx1.wait();
  console.log("   Done");

  // 5. Setup: staking as treasury withdrawer on vault
  console.log("5. Setting staking as treasury withdrawer...");
  const tx2 = await vault.setWithdrawer(deployed.prefiStaking, true);
  await tx2.wait();
  console.log("   Done");

  // 6. Add default markets
  const mkts = DEFAULT_MARKETS[network] || [];
  if (mkts.length > 0) {
    console.log("6. Adding markets...");
    for (const m of mkts) {
      const tx = await vault.addMarket(m.token, m.feeTier);
      await tx.wait();
      console.log(`   ${m.name}: ${m.token} (fee: ${m.feeTier})`);
    }
  }

  // Save deployment
  const deployDir = path.join(__dirname, "..", "..", "deployments");
  if (!fs.existsSync(deployDir)) fs.mkdirSync(deployDir, { recursive: true });

  const info = {
    network,
    timestamp: new Date().toISOString(),
    deployer: deployer.address,
    contracts: deployed,
    addresses: addrs,
    markets: mkts,
  };

  const latest = path.join(deployDir, `${network}-latest.json`);
  fs.writeFileSync(latest, JSON.stringify(info, null, 2));
  console.log(`\nSaved: deployments/${network}-latest.json`);

  // Update config.json
  const configPath = path.join(__dirname, "..", "..", "config.json");
  if (fs.existsSync(configPath)) {
    const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    config.contracts = config.contracts || {};
    config.contracts[network] = deployed;
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
    console.log("Updated config.json");
  }

  console.log("\nDeployed Contracts:");
  console.log("-".repeat(50));
  for (const [name, addr] of Object.entries(deployed)) {
    console.log(`  ${name}: ${addr}`);
  }
  console.log("\nDone.\n");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
