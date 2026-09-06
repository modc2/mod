const hre = require("hardhat");

async function main() {
  // Reuse an existing NativeToken (env NATIVE_TOKEN) so holder balances
  // survive a BlocTime redeploy; otherwise deploy a fresh 1M-supply token.
  let nativeTokenAddress = process.env.NATIVE_TOKEN;
  if (nativeTokenAddress) {
    console.log(`Reusing NativeToken at: ${nativeTokenAddress}`);
  } else {
    const initialSupply = hre.ethers.parseEther("1000000");
    const NativeToken = await hre.ethers.getContractFactory("NativeToken");
    const nativeToken = await NativeToken.deploy(initialSupply);
    await nativeToken.waitForDeployment();
    nativeTokenAddress = await nativeToken.getAddress();
    console.log(`NativeToken deployed to: ${nativeTokenAddress}`);
  }

  // BlocTime v2 params — locks are in SECONDS (block.timestamp), capped at
  // 8 years. BLOC minted = USD value of the stake × seconds locked (linear):
  // the constructor's flat 1x curve IS the default model, so no setPoints.
  const SECONDS_PER_YEAR = 365 * 24 * 3600; // 31,536,000
  const maxLockSeconds = SECONDS_PER_YEAR * 8; // 252,288,000
  const priceUsdMicro = 1_000_000; // $1.00 per NAT until the owner reprices

  const BlocTime = await hre.ethers.getContractFactory("BlocTime");
  const blocTime = await BlocTime.deploy(
    nativeTokenAddress,
    maxLockSeconds,
    priceUsdMicro
  );
  await blocTime.waitForDeployment();
  const blocTimeAddress = await blocTime.getAddress();
  console.log(`BlocTime deployed to: ${blocTimeAddress}`);
  console.log("Model: linear — BLOC = USD value staked × seconds locked (flat 1x curve)");

  // Set Bitcoin-style inflation params — epochs are time-based now:
  // 50 BLOC/epoch, halving every 1460 epochs (~4 years), min 0, epoch = 1 day.
  const initialReward = hre.ethers.parseEther("50");
  const halvingInterval = 1460;
  const minReward = 0;
  const epochLength = 24 * 3600; // 86,400 seconds
  await (await blocTime.setInflationParams(initialReward, halvingInterval, minReward, epochLength)).wait();
  console.log("Inflation params set (50 BLOC/epoch, halving every 1460 epochs, 1-day epochs)");

  // Write deployment info
  const fs = require("fs");
  const path = require("path");
  const info = {
    nativeToken: nativeTokenAddress,
    blocTime: blocTimeAddress,
    address: blocTimeAddress,
    network: hre.network.name,
    chainId: hre.network.config.chainId,
    model: "usd_seconds_linear",
    maxLockSeconds,
    priceUsdMicro,
    secondsPerBlock: 2,
    points: [{ lockSeconds: 0, multiplier: 10000 }],
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
