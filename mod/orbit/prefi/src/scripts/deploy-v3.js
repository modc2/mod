const hre = require("hardhat");
const fs = require('fs');
const path = require('path');

async function main() {
  console.log("🎯 Deploying PreFi V3 Prediction Market...");
  console.log("Network:", hre.network.name);
  console.log("Chain ID:", hre.network.config.chainId);

  const [deployer] = await hre.ethers.getSigners();
  console.log("\n📍 Deployer:", deployer.address);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log("💰 Balance:", hre.ethers.formatEther(balance), "ETH");

  // ── 1. Deploy MockERC20 (stake token for testnet) ──────────────
  console.log("\n📦 Deploying MockERC20 (Stake Token)...");
  const MockERC20 = await hre.ethers.getContractFactory("MockERC20");
  const initialSupply = hre.ethers.parseEther("1000000"); // 1M tokens
  const stakeToken = await MockERC20.deploy("PreFi Stake Token", "PSTK", initialSupply);
  await stakeToken.waitForDeployment();
  const stakeTokenAddr = await stakeToken.getAddress();
  console.log("✅ MockERC20 deployed to:", stakeTokenAddr);

  // ── 2. Deploy PreFiV3 ──────────────────────────────────────────
  console.log("\n🎯 Deploying PreFiV3...");
  const PreFiV3 = await hre.ethers.getContractFactory("PreFiV3");
  const minStake = hre.ethers.parseEther("1"); // 1 token minimum
  const platformFee = 200; // 2% in basis points

  const preFiV3 = await PreFiV3.deploy(
    stakeTokenAddr,     // _stakeToken
    deployer.address,   // _oracle (deployer acts as oracle initially)
    minStake,           // _minStake
    platformFee         // _platformFee
  );
  await preFiV3.waitForDeployment();
  const preFiV3Addr = await preFiV3.getAddress();
  console.log("✅ PreFiV3 deployed to:", preFiV3Addr);

  // ── 3. Create initial market ───────────────────────────────────
  console.log("\n📊 Creating initial ETH/USD market (24h)...");
  const tx = await preFiV3.createMarket(
    "ETH/USD",
    stakeTokenAddr, // token to track
    86400           // 24 hours
  );
  await tx.wait();
  const marketCount = await preFiV3.marketCounter();
  console.log("✅ Market #" + marketCount.toString() + " created (ETH/USD, 24h)");

  // ── 4. Save deployment info ────────────────────────────────────
  const deploymentInfo = {
    network: hre.network.name,
    chainId: hre.network.config.chainId,
    deployer: deployer.address,
    timestamp: new Date().toISOString(),
    contracts: {
      MockERC20: stakeTokenAddr,
      PreFiV3: preFiV3Addr,
    },
    config: {
      stakeToken: stakeTokenAddr,
      oracle: deployer.address,
      minStake: "1.0",
      platformFee: "2%",
    },
    markets: [{
      id: 1,
      asset: "ETH/USD",
      duration: 86400,
    }],
    env: {
      NEXT_PUBLIC_PREFI_V3_ADDRESS: preFiV3Addr,
      NEXT_PUBLIC_STAKE_TOKEN_ADDRESS: stakeTokenAddr,
      NEXT_PUBLIC_ORACLE_ADDRESS: deployer.address,
    },
  };

  // Save to deployments dir
  const deploymentsDir = path.join(__dirname, '..', '..', 'deployments');
  if (!fs.existsSync(deploymentsDir)) {
    fs.mkdirSync(deploymentsDir, { recursive: true });
  }

  const filename = `${hre.network.name}-${Date.now()}.json`;
  const latestFilename = `${hre.network.name}-latest.json`;

  fs.writeFileSync(
    path.join(deploymentsDir, filename),
    JSON.stringify(deploymentInfo, null, 2)
  );
  fs.writeFileSync(
    path.join(deploymentsDir, latestFilename),
    JSON.stringify(deploymentInfo, null, 2)
  );

  // Generate .env.local for the Next.js app
  const envContent = [
    `# PreFi V3 Deployment — ${hre.network.name} — ${new Date().toISOString()}`,
    `NEXT_PUBLIC_PREFI_V3_ADDRESS=${preFiV3Addr}`,
    `NEXT_PUBLIC_STAKE_TOKEN_ADDRESS=${stakeTokenAddr}`,
    `NEXT_PUBLIC_ORACLE_ADDRESS=${deployer.address}`,
    `NEXT_PUBLIC_API_URL=http://localhost:8830`,
    `NEXT_PUBLIC_CHAIN_ID=${hre.network.config.chainId}`,
  ].join('\n');

  const appEnvPath = path.join(__dirname, '..', 'app', '.env.local');
  fs.writeFileSync(appEnvPath, envContent);

  console.log("\n📝 Deployment Summary:");
  console.log(JSON.stringify(deploymentInfo, null, 2));
  console.log(`\n💾 Saved: deployments/${latestFilename}`);
  console.log(`💾 App env: app/.env.local`);

  console.log("\n✨ Deployment complete!");
  console.log("\n🔧 Next steps:");
  console.log("  1. Start the API: cd server && python server.py");
  console.log("  2. Start the app: cd app && npm run dev");
  console.log("  3. Or use: ./scripts/start.sh");

  return deploymentInfo;
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("\n❌ Deployment failed:");
    console.error(error);
    process.exit(1);
  });
