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
  // Blocks are 2s on Base: 43,200 a day, 15,768,000 a year (365 days) — the
  // same unit the inflation epoch counts in. Locks are capped at 8 years.
  const BLOCKS_PER_YEAR = 43200 * 365;
  const maxLockBlocks = BLOCKS_PER_YEAR * 8; // 126,144,000
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

  // Set default multiplier curve — a straight line from 1x at no lock to 8x
  // at the 8-year cap (+8750 bps per year). The points are year marks ON that
  // line, not bends in it: they give the UI readable presets while
  // getMultiplier() returns exactly what one 0 → 8y segment would.
  const points = [
    { blocks: 0, multiplier: 10000 },                    // no lock = 1.000x
    { blocks: BLOCKS_PER_YEAR, multiplier: 18750 },      // 1 year  = 1.875x
    { blocks: BLOCKS_PER_YEAR * 2, multiplier: 27500 },  // 2 years = 2.750x
    { blocks: BLOCKS_PER_YEAR * 4, multiplier: 45000 },  // 4 years = 4.500x
    { blocks: maxLockBlocks, multiplier: 80000 },        // 8 years = 8.000x
  ];
  await blocTime.setPoints(points);
  console.log("Multiplier curve set (linear, 1x → 8x over 8 years)");

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
