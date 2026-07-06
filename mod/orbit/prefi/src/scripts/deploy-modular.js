const hre = require("hardhat");
const { ethers } = require("hardhat");

/**
 * Deploy PreFi Modular with Multiple Oracle Support
 * Supports: Uniswap V3, Chainlink, Polymarket, Custom oracles
 */

const ADDRESSES = {
  base: {
    uniswapV3Factory: "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    weth: "0x4200000000000000000000000000000000000006",
    usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    // Chainlink Price Feeds on Base
    chainlinkFeeds: {
      "ETH/USD": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
      "BTC/USD": "0x64c911996D3c6aC71f9b455B1E8E7266BcbD848F",
    },
  },
  baseSepolia: {
    uniswapV3Factory: "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
    weth: "0x4200000000000000000000000000000000000006",
    usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    chainlinkFeeds: {
      "ETH/USD": "0x4aDC67696bA383F43DD60A9e78F2C97Fbbfc7cb1",
    },
  }
};

async function main() {
  const [deployer] = await ethers.getSigners();
  const network = hre.network.name;
  const chainId = await deployer.getChainId();

  console.log("\n🚀 PreFi Modular Deployment - Multi-Oracle Support");
  console.log("==================================================");
  console.log("Network:", network);
  console.log("Chain ID:", chainId);
  console.log("Deployer:", deployer.address);
  console.log("Balance:", ethers.utils.formatEther(await deployer.getBalance()), "ETH\n");

  const networkAddresses = ADDRESSES[network];
  if (!networkAddresses) {
    throw new Error(`Network ${network} not supported`);
  }

  const deployedContracts = {};

  // 1. Deploy Stake Token (testnet only)
  let stakeTokenAddress;
  if (network === "baseSepolia") {
    console.log("📦 Step 1: Deploying Mock Stake Token...");
    const MockERC20 = await ethers.getContractFactory("MockERC20");
    const stakeToken = await MockERC20.deploy(
      "PreFi Token",
      "PREFI",
      ethers.utils.parseEther("1000000")
    );
    await stakeToken.deployed();
    stakeTokenAddress = stakeToken.address;
    deployedContracts.stakeToken = stakeTokenAddress;
    console.log("✅ Stake Token:", stakeTokenAddress);
  } else {
    stakeTokenAddress = networkAddresses.usdc;
    console.log("💰 Using USDC as stake token:", stakeTokenAddress);
    deployedContracts.stakeToken = stakeTokenAddress;
  }

  // 2. Deploy Uniswap V3 Oracle
  console.log("\n📊 Step 2: Deploying Uniswap V3 Oracle...");
  const UniswapV3Oracle = await ethers.getContractFactory("UniswapV3PriceOracle");
  const uniswapOracle = await UniswapV3Oracle.deploy(networkAddresses.uniswapV3Factory);
  await uniswapOracle.deployed();
  deployedContracts.uniswapV3Oracle = uniswapOracle.address;
  console.log("✅ Uniswap V3 Oracle:", uniswapOracle.address);

  // 3. Deploy Chainlink Oracle
  console.log("\n🔗 Step 3: Deploying Chainlink Oracle...");
  const ChainlinkOracle = await ethers.getContractFactory("ChainlinkPriceOracle");
  const chainlinkOracle = await ChainlinkOracle.deploy();
  await chainlinkOracle.deployed();
  deployedContracts.chainlinkOracle = chainlinkOracle.address;
  console.log("✅ Chainlink Oracle:", chainlinkOracle.address);

  // Register Chainlink feeds
  console.log("   Registering Chainlink price feeds...");
  for (const [pair, feedAddress] of Object.entries(networkAddresses.chainlinkFeeds)) {
    const assetId = ethers.utils.keccak256(ethers.utils.toUtf8Bytes(pair));
    const tx = await chainlinkOracle.addFeed(assetId, feedAddress);
    await tx.wait();
    console.log(`   ✓ ${pair}: ${feedAddress}`);
  }

  // 4. Deploy Polymarket Oracle
  console.log("\n🎲 Step 4: Deploying Polymarket Oracle...");
  const PolymarketOracle = await ethers.getContractFactory("PolymarketOracle");
  const polymarketOracle = await PolymarketOracle.deploy(deployer.address);
  await polymarketOracle.deployed();
  deployedContracts.polymarketOracle = polymarketOracle.address;
  console.log("✅ Polymarket Oracle:", polymarketOracle.address);

  // 5. Deploy PreFi Modular
  console.log("\n🎯 Step 5: Deploying PreFi Modular...");
  const minStake = ethers.utils.parseEther("0.001");
  const platformFee = 100; // 1%

  const PreFiModular = await ethers.getContractFactory("PreFiModular");
  const preFi = await PreFiModular.deploy(stakeTokenAddress, minStake, platformFee);
  await preFi.deployed();
  deployedContracts.preFiModular = preFi.address;
  console.log("✅ PreFi Modular:", preFi.address);

  // 6. Register Oracles with PreFi
  console.log("\n🔌 Step 6: Registering Oracles...");

  // OracleType enum: UNISWAP_V3 = 0, CHAINLINK = 1, POLYMARKET = 2, CUSTOM = 3
  await (await preFi.registerOracle(0, uniswapOracle.address)).wait();
  console.log("✓ Uniswap V3 registered (Type: 0)");

  await (await preFi.registerOracle(1, chainlinkOracle.address)).wait();
  console.log("✓ Chainlink registered (Type: 1)");

  await (await preFi.registerOracle(2, polymarketOracle.address)).wait();
  console.log("✓ Polymarket registered (Type: 2)");

  // 7. Create Sample Markets
  console.log("\n📈 Step 7: Creating Sample Markets...");

  // Market 1: ETH/USD with Uniswap V3
  const ethUsdData = ethers.utils.defaultAbiCoder.encode(
    ["address", "address", "uint24", "uint32"],
    [networkAddresses.weth, networkAddresses.usdc, 3000, 1800] // 0.3% fee, 30min TWAP
  );
  const market1 = await preFi.createMarket(
    "ETH/USD (Uniswap V3 TWAP)",
    ethers.utils.keccak256(ethers.utils.toUtf8Bytes("ETH/USD")),
    0, // UNISWAP_V3
    ethUsdData,
    7 * 24 * 60 * 60 // 7 days
  );
  await market1.wait();
  console.log("✓ Market 1: ETH/USD (Uniswap V3)");

  // Market 2: ETH/USD with Chainlink
  if (networkAddresses.chainlinkFeeds["ETH/USD"]) {
    const market2 = await preFi.createMarket(
      "ETH/USD (Chainlink)",
      ethers.utils.keccak256(ethers.utils.toUtf8Bytes("ETH/USD")),
      1, // CHAINLINK
      "0x", // No additional data needed
      7 * 24 * 60 * 60
    );
    await market2.wait();
    console.log("✓ Market 2: ETH/USD (Chainlink)");
  }

  // Market 3: Polymarket - Example prediction
  const polymarketId = ethers.utils.keccak256(ethers.utils.toUtf8Bytes("BTC_100K_2024"));
  const market3 = await preFi.createMarket(
    "Will Bitcoin reach $100k in 2024?",
    polymarketId,
    2, // POLYMARKET
    ethers.utils.defaultAbiCoder.encode(["bool"], [true]), // Get YES price
    30 * 24 * 60 * 60 // 30 days
  );
  await market3.wait();
  console.log("✓ Market 3: BTC $100k Prediction (Polymarket)");

  // Summary
  console.log("\n📋 Deployment Summary");
  console.log("==================================================");
  console.log("Network:", network);
  console.log("Chain ID:", chainId);
  console.log("\n📦 Deployed Contracts:");
  console.log("- Stake Token:", deployedContracts.stakeToken);
  console.log("- Uniswap V3 Oracle:", deployedContracts.uniswapV3Oracle);
  console.log("- Chainlink Oracle:", deployedContracts.chainlinkOracle);
  console.log("- Polymarket Oracle:", deployedContracts.polymarketOracle);
  console.log("- PreFi Modular:", deployedContracts.preFiModular);
  console.log("\n⚙️ Configuration:");
  console.log("- Min Stake:", ethers.utils.formatEther(minStake), "tokens");
  console.log("- Platform Fee:", platformFee / 100, "%");
  console.log("\n✅ Sample Markets Created:");
  console.log("1. ETH/USD (Uniswap V3 TWAP)");
  console.log("2. ETH/USD (Chainlink)");
  console.log("3. Bitcoin $100k Prediction (Polymarket)");

  // Save deployment info
  const fs = require("fs");
  const deploymentInfo = {
    network,
    chainId: chainId.toString(),
    timestamp: new Date().toISOString(),
    deployer: deployer.address,
    contracts: deployedContracts,
    config: {
      minStake: minStake.toString(),
      platformFee,
    },
    markets: {
      1: "ETH/USD (Uniswap V3)",
      2: "ETH/USD (Chainlink)",
      3: "BTC $100k (Polymarket)",
    },
    networkAddresses,
  };

  const filename = `deployment-modular-${network}-${Date.now()}.json`;
  fs.writeFileSync(
    `./deployments/${filename}`,
    JSON.stringify(deploymentInfo, null, 2)
  );
  console.log(`\n💾 Deployment saved to: deployments/${filename}`);

  // Verification commands
  if (network !== "hardhat" && network !== "localhost") {
    console.log("\n🔍 Verify contracts with:");
    console.log(`npx hardhat verify --network ${network} ${deployedContracts.uniswapV3Oracle} ${networkAddresses.uniswapV3Factory}`);
    console.log(`npx hardhat verify --network ${network} ${deployedContracts.chainlinkOracle}`);
    console.log(`npx hardhat verify --network ${network} ${deployedContracts.polymarketOracle} ${deployer.address}`);
    console.log(`npx hardhat verify --network ${network} ${deployedContracts.preFiModular} ${stakeTokenAddress} ${minStake} ${platformFee}`);
  }

  console.log("\n✨ Deployment complete!\n");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
