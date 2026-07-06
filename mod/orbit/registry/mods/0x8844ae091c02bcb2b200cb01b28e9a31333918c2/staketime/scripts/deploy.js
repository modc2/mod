const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deploying with: ${deployer.address}`);

  let nonce = await deployer.getNonce();
  const send = (opts) => ({ ...opts, nonce: nonce++ });

  // ── 1. Deploy Mod ERC20 token (zero initial supply) ───────────────
  const ModToken = await hre.ethers.getContractFactory("Mod");
  const modToken = await ModToken.deploy("StakeTimeNet", "STN", send({}));
  await modToken.waitForDeployment();
  const modTokenAddress = await modToken.getAddress();
  console.log(`Mod (ERC20) deployed to: ${modTokenAddress}`);

  // ── 2. Deploy StakeTime (ERC20 + staking) ────────────────────────
  const maxLockBlocks = 100000;
  const maxStakersPerValidator = 100;
  const defaultCommissionBps = 1000;   // 10%

  const StakeTime = await hre.ethers.getContractFactory("StakeTime");
  const staking = await StakeTime.deploy(
    modTokenAddress,
    maxLockBlocks,
    maxStakersPerValidator,
    defaultCommissionBps,
    send({})
  );
  await staking.waitForDeployment();
  const stakingAddress = await staking.getAddress();
  console.log(`StakeTime (STT) deployed to: ${stakingAddress}`);

  // ── 3. Deploy ConsensusYuma (scoring + emissions) ─────────────────
  const epochLength = 43200;           // ~1 day on Base
  const emissionRate = hre.ethers.parseEther("100"); // 100 tokens per epoch
  const decayBps = 500;                              // 5% decay

  const ConsensusYuma = await hre.ethers.getContractFactory("ConsensusYuma");
  const consensus = await ConsensusYuma.deploy(
    modTokenAddress,       // subnet (emission token)
    stakingAddress,      // staking contract (reads validator/stake data)
    emissionRate,
    decayBps,
    epochLength,
    send({})
  );
  await consensus.waitForDeployment();
  const consensusAddress = await consensus.getAddress();
  console.log(`ConsensusYuma deployed to: ${consensusAddress}`);

  // ── 4. Wire permissions ───────────────────────────────────────────
  // ConsensusYuma mints new Mod tokens for emissions
  await (await modToken.setMinter(consensusAddress, send({}))).wait();
  console.log(`Mod minter set to ConsensusYuma`);

  // ── 5. Deploy governance token for Registry ───────────────────────
  const GovToken = await hre.ethers.getContractFactory("Mod");
  const govToken = await GovToken.deploy("Governance", "GOV", send({}));
  await govToken.waitForDeployment();
  const govTokenAddress = await govToken.getAddress();
  console.log(`Governance token deployed to: ${govTokenAddress}`);

  // ── 6. Deploy Registry ────────────────────────────────────────────
  const immunityPeriod = 43200;
  const registrationCost = hre.ethers.parseEther("1000");

  const Registry = await hre.ethers.getContractFactory("Registry");
  const registry = await Registry.deploy(immunityPeriod, govTokenAddress, registrationCost, send({}));
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log(`Registry deployed to: ${registryAddress}`);

  // ── 7. Register genesis subnet ────────────────────────────────────
  await (await govToken.approve(registryAddress, registrationCost, send({}))).wait();
  await (await registry.registerSubnet("genesis", modTokenAddress, stakingAddress, consensusAddress, send({}))).wait();
  console.log(`Genesis subnet registered (locked ${hre.ethers.formatEther(registrationCost)} GOV)`);

  // ── 8. Save deployment info (network-specific) ────────────────────
  const fs = require("fs");
  const path = require("path");
  const configPath = path.join(__dirname, "..", "config.json");
  const network = hre.network.name;

  let config = {};
  if (fs.existsSync(configPath)) {
    config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  }

  if (!config.contracts) config.contracts = {};
  config.contracts[network] = {
    mod: modTokenAddress,
    staking: stakingAddress,
    consensus: consensusAddress,
    governanceToken: govTokenAddress,
    registry: registryAddress,
    chainId: hre.network.config.chainId,
    emissionRate: emissionRate.toString(),
    decayBps,
    epochLength,
    immunityPeriod,
    registrationCost: registrationCost.toString(),
    maxLockBlocks,
    maxStakersPerValidator,
    defaultCommissionBps,
    deployedAt: new Date().toISOString(),
  };

  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  console.log(`Deployment info saved to config.json [${network}]`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
