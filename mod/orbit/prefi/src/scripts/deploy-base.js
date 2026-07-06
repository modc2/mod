const hre = require("hardhat");
const { ethers } = require("hardhat");

/**
 * Deployment script for PreFi V3 on Base (Mainnet & Testnet)
 * Run with:
 * npx hardhat run scripts/deploy-base.js --network base
 * npx hardhat run scripts/deploy-base.js --network baseSepolia
 */

// Base contract addresses
const ADDRESSES = {
  // Base Mainnet (Chain ID: 8453)
  base: {
    uniswapV3Factory: "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    weth: "0x4200000000000000000000000000000000000006",
    usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    dai: "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
  },
  // Base Sepolia (Chain ID: 84532)
  baseSepolia: {
    uniswapV3Factory: "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
    weth: "0x4200000000000000000000000000000000000006",
    usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  }
};

async function main() {
  const [deployer] = await ethers.getSigners();
  const network = hre.network.name;
  const chainId = await deployer.getChainId();

  console.log("\n🚀 PreFi V3 Deployment on Base");
  console.log("================================");
  console.log("Network:", network);
  console.log("Chain ID:", chainId);
  console.log("Deployer:", deployer.address);
  console.log("Balance:", ethers.utils.formatEther(await deployer.getBalance()), "ETH\n");

  // Get network-specific addresses
  const networkAddresses = ADDRESSES[network];
  if (!networkAddresses) {
    throw new Error(`Network ${network} not supported. Use 'base' or 'baseSepolia'`);
  }

  console.log("Network Configuration:");
  console.log("- Uniswap V3 Factory:", networkAddresses.uniswapV3Factory);
  console.log("- WETH:", networkAddresses.weth);
  console.log("- USDC:", networkAddresses.usdc);

  // Step 1: Deploy Uniswap V3 Oracle
  console.log("\n📊 Step 1: Deploying UniswapV3Oracle...");
  const UniswapV3Oracle = await ethers.getContractFactory("UniswapV3Oracle");
  const oracle = await UniswapV3Oracle.deploy(networkAddresses.uniswapV3Factory);
  await oracle.deployed();
  console.log("✅ UniswapV3Oracle deployed to:", oracle.address);

  // Step 2: Deploy Mock ERC20 for testing (only on testnet)
  let stakeTokenAddress;
  if (network === "baseSepolia") {
    console.log("\n🪙 Step 2: Deploying Mock Stake Token (Testnet)...");
    const MockERC20 = await ethers.getContractFactory("MockERC20");
    const stakeToken = await MockERC20.deploy("PreFi Stake Token", "PREFI", ethers.utils.parseEther("1000000"));
    await stakeToken.deployed();
    stakeTokenAddress = stakeToken.address;
    console.log("✅ Mock Stake Token deployed to:", stakeTokenAddress);
  } else {
    // On mainnet, use USDC as stake token
    stakeTokenAddress = networkAddresses.usdc;
    console.log("\n💰 Step 2: Using USDC as stake token:", stakeTokenAddress);
  }

  // Step 3: Deploy PreFi V3
  console.log("\n🎯 Step 3: Deploying PreFiV3...");
  const minStake = ethers.utils.parseEther("0.001"); // 0.001 tokens
  const platformFee = 100; // 1%

  const PreFiV3 = await ethers.getContractFactory("PreFiV3");
  const preFi = await PreFiV3.deploy(
    stakeTokenAddress,
    oracle.address, // Oracle address
    minStake,
    platformFee
  );
  await preFi.deployed();
  console.log("✅ PreFiV3 deployed to:", preFi.address);

  // Step 4: Create initial market
  console.log("\n📈 Step 4: Creating initial market...");
  const marketDuration = 7 * 24 * 60 * 60; // 7 days
  const tx = await preFi.createMarket(
    "ETH/USD",
    networkAddresses.weth,
    marketDuration
  );
  const receipt = await tx.wait();
  console.log("✅ Initial ETH/USD market created");

  // Summary
  console.log("\n📋 Deployment Summary");
  console.log("================================");
  console.log("Network:", network);
  console.log("Chain ID:", chainId);
  console.log("\nDeployed Contracts:");
  console.log("- UniswapV3Oracle:", oracle.address);
  console.log("- Stake Token:", stakeTokenAddress);
  console.log("- PreFiV3:", preFi.address);
  console.log("\nConfiguration:");
  console.log("- Min Stake:", ethers.utils.formatEther(minStake), "tokens");
  console.log("- Platform Fee:", platformFee / 100, "%");
  console.log("- Default Market Duration:", marketDuration / 86400, "days");

  // Save deployment addresses
  const fs = require("fs");
  const deploymentInfo = {
    network,
    chainId: chainId.toString(),
    timestamp: new Date().toISOString(),
    deployer: deployer.address,
    contracts: {
      uniswapV3Oracle: oracle.address,
      stakeToken: stakeTokenAddress,
      preFiV3: preFi.address,
    },
    config: {
      minStake: minStake.toString(),
      platformFee,
      marketDuration,
    },
    networkAddresses,
  };

  const filename = `deployment-${network}-${Date.now()}.json`;
  fs.writeFileSync(
    `./deployments/${filename}`,
    JSON.stringify(deploymentInfo, null, 2)
  );
  console.log(`\n💾 Deployment info saved to: deployments/${filename}`);

  // Verification instructions
  if (network !== "hardhat" && network !== "localhost") {
    console.log("\n🔍 Verify contracts with:");
    console.log(`npx hardhat verify --network ${network} ${oracle.address} ${networkAddresses.uniswapV3Factory}`);
    console.log(`npx hardhat verify --network ${network} ${preFi.address} ${stakeTokenAddress} ${oracle.address} ${minStake} ${platformFee}`);
  }

  console.log("\n✅ Deployment complete!\n");
}

// Contract for Mock ERC20 (add to contracts/ if needed)
const mockERC20Source = `
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockERC20 is ERC20 {
    constructor(
        string memory name,
        string memory symbol,
        uint256 initialSupply
    ) ERC20(name, symbol) {
        _mint(msg.sender, initialSupply);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
`;

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
