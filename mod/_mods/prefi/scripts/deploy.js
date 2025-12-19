const hre = require("hardhat");

async function main() {
  console.log("🚀 Deploying Prefi Prediction Market with Modular Oracles...");
  console.log("Network:", hre.network.name);
  
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);
  console.log("Account balance:", (await deployer.getBalance()).toString());
  
  // Deploy PriceOracle (modular version)
  console.log("\n📊 Deploying Modular PriceOracle...");
  const PriceOracle = await hre.ethers.getContractFactory("contracts/oracles/PriceOracle.sol:PriceOracle");
  const oracle = await PriceOracle.deploy();
  await oracle.deployed();
  console.log("✅ PriceOracle deployed to:", oracle.address);
  
  // Deploy CoinGecko Adapter
  console.log("\n🦎 Deploying CoinGecko Adapter...");
  const CoinGeckoAdapter = await hre.ethers.getContractFactory("CoinGeckoAdapter");
  const cgAdapter = await CoinGeckoAdapter.deploy(oracle.address);
  await cgAdapter.deployed();
  console.log("✅ CoinGeckoAdapter deployed to:", cgAdapter.address);
  
  // Deploy CoinMarketCap Adapter
  console.log("\n📈 Deploying CoinMarketCap Adapter...");
  const CoinMarketCapAdapter = await hre.ethers.getContractFactory("CoinMarketCapAdapter");
  const cmcAdapter = await CoinMarketCapAdapter.deploy(oracle.address);
  await cmcAdapter.deployed();
  console.log("✅ CoinMarketCapAdapter deployed to:", cmcAdapter.address);
  
  // Add adapters to oracle
  console.log("\n🔗 Registering adapters with oracle...");
  await oracle.addAdapter(cgAdapter.address);
  console.log("✅ CoinGecko adapter registered");
  await oracle.addAdapter(cmcAdapter.address);
  console.log("✅ CoinMarketCap adapter registered");
  
  // Deploy PredictionMarket
  console.log("\n🎯 Deploying PredictionMarket...");
  const PredictionMarket = await hre.ethers.getContractFactory("PredictionMarket");
  const market = await PredictionMarket.deploy(oracle.address);
  await market.deployed();
  console.log("✅ PredictionMarket deployed to:", market.address);
  
  // Save deployment info
  const deploymentInfo = {
    network: hre.network.name,
    chainId: hre.network.config.chainId,
    deployer: deployer.address,
    contracts: {
      PriceOracle: oracle.address,
      CoinGeckoAdapter: cgAdapter.address,
      CoinMarketCapAdapter: cmcAdapter.address,
      PredictionMarket: market.address
    },
    timestamp: new Date().toISOString()
  };
  
  console.log("\n📝 Deployment Summary:");
  console.log(JSON.stringify(deploymentInfo, null, 2));
  
  // Save to file
  const fs = require('fs');
  const path = require('path');
  const deploymentsDir = path.join(__dirname, '../deployments');
  
  if (!fs.existsSync(deploymentsDir)) {
    fs.mkdirSync(deploymentsDir, { recursive: true });
  }
  
  const filename = `${hre.network.name}-${Date.now()}.json`;
  fs.writeFileSync(
    path.join(deploymentsDir, filename),
    JSON.stringify(deploymentInfo, null, 2)
  );
  
  console.log(`\n💾 Deployment info saved to: deployments/${filename}`);
  
  // Verification instructions
  if (hre.network.name !== 'hardhat' && hre.network.name !== 'ganache') {
    console.log("\n🔍 To verify contracts, run:");
    console.log(`npx hardhat verify --network ${hre.network.name} ${oracle.address}`);
    console.log(`npx hardhat verify --network ${hre.network.name} ${cgAdapter.address} ${oracle.address}`);
    console.log(`npx hardhat verify --network ${hre.network.name} ${cmcAdapter.address} ${oracle.address}`);
    console.log(`npx hardhat verify --network ${hre.network.name} ${market.address} ${oracle.address}`);
  }
  
  console.log("\n✨ Deployment complete!");
  console.log("\n📚 Next steps:");
  console.log("1. Add assets to adapters using addAsset()");
  console.log("2. Update prices from off-chain oracles");
  console.log("3. Configure PredictionMarket with approved collateral");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
