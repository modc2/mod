const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deploying with: ${deployer.address}`);

  let nonce = await deployer.getNonce();
  const send = (opts) => ({ ...opts, nonce: nonce++ });

  // ── 1. Deploy Subnet ERC20 token (1M initial supply) ──────────────
  const initialSupply = hre.ethers.parseEther("1000000");
  const Subnet = await hre.ethers.getContractFactory("Subnet");
  const subnet = await Subnet.deploy("StakeTimeNet", "STN", initialSupply, send({}));
  await subnet.waitForDeployment();
  const subnetAddress = await subnet.getAddress();
  console.log(`Subnet (ERC20) deployed to: ${subnetAddress}`);

  // ── 2. Deploy Staking (validator registration + staking) ──────────
  const maxLockBlocks = 100000;
  const maxStakersPerValidator = 100;
  const defaultCommissionBps = 1000;   // 10%

  const Staking = await hre.ethers.getContractFactory("Staking");
  const staking = await Staking.deploy(
    subnetAddress,       // nativeToken (staked token)
    maxLockBlocks,
    maxStakersPerValidator,
    defaultCommissionBps,
    send({})
  );
  await staking.waitForDeployment();
  const stakingAddress = await staking.getAddress();
  console.log(`Staking deployed to: ${stakingAddress}`);

  // ── 3. Deploy ConsensusYuma (scoring + emissions) ─────────────────
  const epochLength = 43200;           // ~1 day on Base
  const emissionRate = hre.ethers.parseEther("100"); // 100 tokens per epoch
  const decayBps = 500;                              // 5% decay

  const ConsensusYuma = await hre.ethers.getContractFactory("ConsensusYuma");
  const consensus = await ConsensusYuma.deploy(
    subnetAddress,       // subnet (emission token)
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
  // ConsensusYuma mints new Subnet tokens for emissions
  await (await subnet.setMinter(consensusAddress, send({}))).wait();
  console.log(`Subnet minter set to ConsensusYuma`);

  // ── 5. Deploy governance token for Registry ───────────────────────
  const govToken = await Subnet.deploy("Governance", "GOV", hre.ethers.parseEther("10000000"), send({}));
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
  await (await registry.registerSubnet("genesis", subnetAddress, stakingAddress, consensusAddress, send({}))).wait();
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
    subnet: subnetAddress,
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
