const hre = require("hardhat");

async function main() {
  // Deploy NativeToken (1M supply)
  const initialSupply = hre.ethers.parseEther("1000000");
  const NativeToken = await hre.ethers.getContractFactory("NativeToken");
  const nativeToken = await NativeToken.deploy(initialSupply);
  await nativeToken.waitForDeployment();
  const nativeTokenAddress = await nativeToken.getAddress();
  console.log(`NativeToken deployed to: ${nativeTokenAddress}`);

  // BlocTime params
  const maxLockBlocks = 100000;
  const distributionPercentage = 5000; // 50%

  // Deploy BlocTime
  const BlocTime = await hre.ethers.getContractFactory("BlocTime");
  const blocTime = await BlocTime.deploy(
    nativeTokenAddress,
    maxLockBlocks,
    distributionPercentage
  );
  await blocTime.waitForDeployment();
  const blocTimeAddress = await blocTime.getAddress();
  console.log(`BlocTime deployed to: ${blocTimeAddress}`);

  // Set default multiplier curve
  const points = [
    { blocks: 0, multiplier: 10000 },       // 0 blocks = 1.0x
    { blocks: 10000, multiplier: 15000 },    // 10k blocks = 1.5x
    { blocks: 50000, multiplier: 20000 },    // 50k blocks = 2.0x
    { blocks: 100000, multiplier: 30000 },   // 100k blocks = 3.0x
  ];
  await blocTime.setPoints(points);
  console.log("Multiplier curve set");

  // Set Bitcoin-style inflation params
  // 50 BLOC/epoch, halving every 1460 epochs (~4 years), min 0, epoch = 43200 blocks (~1 day)
  const initialReward = hre.ethers.parseEther("50");
  const halvingInterval = 1460;
  const minReward = 0;
  const epochLength = 43200;
  await blocTime.setInflationParams(initialReward, halvingInterval, minReward, epochLength);
  console.log("Inflation params set (Bitcoin defaults: 50 BLOC/epoch, halving every 1460 epochs)");

  // Write deployment info
  const fs = require("fs");
  const path = require("path");
  const info = {
    nativeToken: nativeTokenAddress,
    blocTime: blocTimeAddress,
    address: blocTimeAddress,
    network: hre.network.name,
    chainId: hre.network.config.chainId,
    maxLockBlocks,
    distributionPercentage,
    points: points.map(p => ({ blocks: p.blocks, multiplier: p.multiplier })),
    inflation: {
      initialRewardPerEpoch: "50",
      halvingInterval,
      minRewardPerEpoch: "0",
      epochLength,
    },
    deployedAt: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(__dirname, "..", "deployment.json"),
    JSON.stringify(info, null, 2)
  );
  console.log("Deployment info saved to deployment.json");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
