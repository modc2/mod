const hre = require("hardhat");

// Canonical Circle USDC per chain — the dollar the treasury takes in.
// Override with RESERVE_TOKEN; unknown chains without one get a MockUSD.
const RESERVE_TOKENS = {
  1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
  8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
  84532: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  11155111: "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
  10: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
  42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
  137: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
};

async function main() {
  const chainId = hre.network.config.chainId;

  // Reuse an existing NativeToken (env NATIVE_TOKEN) so holder balances
  // survive a BlocTime redeploy; otherwise deploy a fresh 1M-supply token.
  let nativeTokenAddress = process.env.NATIVE_TOKEN;
  let freshToken = false;
  if (nativeTokenAddress) {
    console.log(`Reusing NativeToken at: ${nativeTokenAddress}`);
  } else {
    const initialSupply = hre.ethers.parseEther("1000000");
    const NativeToken = await hre.ethers.getContractFactory("NativeToken");
    const nativeToken = await NativeToken.deploy(initialSupply);
    await nativeToken.waitForDeployment();
    nativeTokenAddress = await nativeToken.getAddress();
    freshToken = true;
    console.log(`NativeToken deployed to: ${nativeTokenAddress}`);
  }

  // Treasury — mints NativeToken 1:1 per dollar of reserve deposited. Only a
  // fresh token can hand over mint keys; a reused pre-treasury token can't.
  let treasuryAddress = "";
  let reserveTokenAddress = "";
  if (freshToken) {
    reserveTokenAddress = process.env.RESERVE_TOKEN || RESERVE_TOKENS[chainId] || "";
    if (!reserveTokenAddress) {
      const MockUSD = await hre.ethers.getContractFactory("MockUSD");
      const mock = await MockUSD.deploy();
      await mock.waitForDeployment();
      reserveTokenAddress = await mock.getAddress();
      console.log(`No known dollar on chain ${chainId} — MockUSD deployed to: ${reserveTokenAddress}`);
    }
    const Treasury = await hre.ethers.getContractFactory("Treasury");
    const treasury = await Treasury.deploy(nativeTokenAddress, reserveTokenAddress);
    await treasury.waitForDeployment();
    treasuryAddress = await treasury.getAddress();
    console.log(`Treasury deployed to: ${treasuryAddress} (reserve ${reserveTokenAddress})`);

    const token = await hre.ethers.getContractAt("NativeToken", nativeTokenAddress);
    await (await token.transferOwnership(treasuryAddress)).wait();
    console.log("NativeToken mint keys handed to the treasury — 1 NTV per $1 deposited");
  } else {
    console.log("Skipping treasury: reused NativeToken has no owner-mint to hand over");
  }

  // BlocTime v2 params — locks are in SECONDS (block.timestamp), capped at
  // 8 years by default (the owner can change it later via setParams).
  // BLOC minted = USD value of the stake × seconds locked (linear): the
  // constructor's flat 1x curve IS the default model, so no setPoints.
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
    treasury: treasuryAddress,
    reserveToken: reserveTokenAddress,
    network: hre.network.name,
    chainId,
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
