const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deploying with: ${deployer.address}`);

  let nonce = await deployer.getNonce();
  const send = (opts) => ({ ...opts, nonce: nonce++ });

  // ── 1. Deploy Owner (EOA mode, admin = deployer) ────────────────
  const OwnerContract = await hre.ethers.getContractFactory("Owner");
  const owner = await OwnerContract.deploy(deployer.address, send({}));
  await owner.waitForDeployment();
  const ownerAddress = await owner.getAddress();
  console.log(`Owner deployed to: ${ownerAddress}`);

  // ── 2. Deploy Mod ERC20 token (zero initial supply) ─────────────
  const ModToken = await hre.ethers.getContractFactory("Mod");
  const modToken = await ModToken.deploy("StakeTimeNet", "STN", send({}));
  await modToken.waitForDeployment();
  const modTokenAddress = await modToken.getAddress();
  console.log(`Mod (ERC20) deployed to: ${modTokenAddress}`);

  // ── 3. Deploy StakeTime (ERC20 + staking) ───────────────────────
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

  // ── 4. Deploy ConsensusYuma (scoring + emissions) ───────────────
  const epochLength = 43200;           // ~1 day on Base
  const emissionRate = hre.ethers.parseEther("100"); // 100 tokens per epoch
  const decayBps = 500;                              // 5% decay

  const ConsensusYuma = await hre.ethers.getContractFactory("ConsensusYuma");
  const consensus = await ConsensusYuma.deploy(
    modTokenAddress,       // mod token (emission token)
    stakingAddress,        // staking contract (reads validator/stake data)
    emissionRate,
    decayBps,
    epochLength,
    send({})
  );
  await consensus.waitForDeployment();
  const consensusAddress = await consensus.getAddress();
  console.log(`ConsensusYuma deployed to: ${consensusAddress}`);

  // ── 5. Wire permissions via Owner ───────────────────────────────
  // Transfer ownership of all contracts to Owner
  await (await modToken.transferOwnership(ownerAddress, send({}))).wait();
  await (await staking.transferOwnership(ownerAddress, send({}))).wait();
  await (await consensus.transferOwnership(ownerAddress, send({}))).wait();
  console.log(`All contracts ownership transferred to Owner`);

  // Owner sets consensus as minter on Mod token
  const setMinterData = modToken.interface.encodeFunctionData("setMinter", [consensusAddress]);
  await (await owner.execute(modTokenAddress, setMinterData, send({}))).wait();
  console.log(`Mod minter set to ConsensusYuma (via Owner)`);

  // ── 6. Deploy governance token for Registry ─────────────────────
  const GovToken = await hre.ethers.getContractFactory("Mod");
  const govToken = await GovToken.deploy("Governance", "GOV", send({}));
  await govToken.waitForDeployment();
  const govTokenAddress = await govToken.getAddress();
  console.log(`Governance token deployed to: ${govTokenAddress}`);

  // ── 7. Deploy Registry ──────────────────────────────────────────
  const immunityPeriod = 43200;
  const registrationCost = hre.ethers.parseEther("1000");

  const Registry = await hre.ethers.getContractFactory("Registry");
  const registry = await Registry.deploy(immunityPeriod, govTokenAddress, registrationCost, send({}));
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log(`Registry deployed to: ${registryAddress}`);

  // Transfer Registry ownership to Owner
  await (await registry.transferOwnership(ownerAddress, send({}))).wait();
  console.log(`Registry ownership transferred to Owner`);

  // ── 8. Register genesis mod ─────────────────────────────────────
  // Governance token needs minter set so deployer can mint for registration
  await (await govToken.setMinter(deployer.address, send({}))).wait();
  await (await govToken.mint(deployer.address, registrationCost, send({}))).wait();
  await (await govToken.approve(registryAddress, registrationCost, send({}))).wait();
  await (await registry.registerMod("genesis", modTokenAddress, stakingAddress, consensusAddress, send({}))).wait();
  console.log(`Genesis mod registered (locked ${hre.ethers.formatEther(registrationCost)} GOV)`);

  // Transfer GOV token ownership to Owner too
  await (await govToken.transferOwnership(ownerAddress, send({}))).wait();
  console.log(`GOV token ownership transferred to Owner`);

  // ── 9. Save deployment info (network-specific) ──────────────────
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
    owner: ownerAddress,
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
