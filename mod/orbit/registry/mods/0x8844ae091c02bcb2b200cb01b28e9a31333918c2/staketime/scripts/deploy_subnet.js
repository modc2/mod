/**
 * Deploy a single mod: Mod ERC20 + Staking + Consensus + register in Registry.
 *
 * Supports consensus types: yuma (default), linear, staked
 *
 * Usage:
 *   SUBNET_PARAMS='{"name":"MyNet","symbol":"MNT","consensusType":"yuma",...}' \
 *   npx hardhat run scripts/deploy_subnet.js --network base_sepolia
 */
const hre = require("hardhat");

async function main() {
  const raw = process.env.SUBNET_PARAMS;
  if (!raw) throw new Error("SUBNET_PARAMS env var required (JSON)");

  const params = JSON.parse(raw);
  const {
    name,
    symbol,
    maxLockBlocks = 100000,
    maxStakersPerValidator = 100,
    defaultCommissionBps = 1000,
    epochLength = 43200,
    emissionRate = "100",
    decayBps = 500,
    consensusType = "yuma",
    registryAddress,
  } = params;

  if (!name || !symbol) throw new Error("name and symbol required");
  if (!registryAddress) throw new Error("registryAddress required");

  const validTypes = ["yuma", "linear", "staked"];
  if (!validTypes.includes(consensusType)) {
    throw new Error(`Invalid consensusType: ${consensusType}. Must be one of: ${validTypes.join(", ")}`);
  }

  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deploying mod "${name}" (${symbol}) [${consensusType}] with: ${deployer.address}`);

  let nonce = await deployer.getNonce();
  const send = (opts) => ({ ...opts, nonce: nonce++ });

  // 1. Deploy Mod ERC20 (zero initial supply)
  const ModToken = await hre.ethers.getContractFactory("Mod");
  const modToken = await ModToken.deploy(name, symbol, send({}));
  await modToken.waitForDeployment();
  const modTokenAddress = await modToken.getAddress();
  console.log(`Mod token deployed: ${modTokenAddress}`);

  // 2. Deploy StakeTime (ERC20 + staking)
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
  console.log(`StakeTime (STT) deployed: ${stakingAddress}`);

  // 3. Deploy Consensus (selected type)
  const emRate = hre.ethers.parseEther(emissionRate);
  let consensus;
  const contractNames = {
    yuma: "ConsensusYuma",
    linear: "ConsensusLinear",
    staked: "ConsensusStaked",
  };
  const factoryName = contractNames[consensusType];
  const Factory = await hre.ethers.getContractFactory(factoryName);

  if (consensusType === "yuma") {
    // Yuma has an extra decayBps parameter
    consensus = await Factory.deploy(
      modTokenAddress, stakingAddress, emRate, decayBps, epochLength, send({})
    );
  } else {
    // Linear and Staked share the same constructor
    consensus = await Factory.deploy(
      modTokenAddress, stakingAddress, emRate, epochLength, send({})
    );
  }
  await consensus.waitForDeployment();
  const consensusAddress = await consensus.getAddress();
  console.log(`${factoryName} deployed: ${consensusAddress}`);

  // 4. Set consensus as minter
  await (await modToken.setMinter(consensusAddress, send({}))).wait();
  console.log(`Minter set to ${factoryName}`);

  // 5. Register in Registry
  const Registry = await hre.ethers.getContractFactory("Registry");
  const registry = Registry.attach(registryAddress);

  const govTokenAddr = await registry.governanceToken();
  const GovToken = await hre.ethers.getContractFactory("Mod");
  const govToken = GovToken.attach(govTokenAddr);

  const cost = await registry.registrationCost();
  if (cost > 0n) {
    await (await govToken.approve(registryAddress, cost, send({}))).wait();
    console.log(`Approved ${hre.ethers.formatEther(cost)} GOV for registration`);
  }

  const tx = await registry.registerSubnet(name, modTokenAddress, stakingAddress, consensusAddress, send({}));
  const receipt = await tx.wait();
  console.log(`Mod registered in Registry (tx: ${receipt.hash})`);

  const result = {
    mod: modTokenAddress,
    staking: stakingAddress,
    consensus: consensusAddress,
    consensusType,
    name,
    symbol,
    maxLockBlocks,
    maxStakersPerValidator,
    defaultCommissionBps,
    epochLength,
    emissionRate,
    decayBps,
    tx: receipt.hash,
  };

  console.log(`\n__RESULT__${JSON.stringify(result)}__END__`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
