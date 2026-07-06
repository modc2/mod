/**
 * Deploy a single subnet: Subnet ERC20 + Staking + ConsensusYuma + register in Registry.
 *
 * Usage:
 *   SUBNET_PARAMS='{"name":"MyNet","symbol":"MNT","initialSupply":"1000000",...}' \
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
    initialSupply = "1000000",
    maxLockBlocks = 100000,
    maxStakersPerValidator = 100,
    defaultCommissionBps = 1000,
    epochLength = 43200,
    emissionRate = "100",
    decayBps = 500,
    registryAddress,
  } = params;

  if (!name || !symbol) throw new Error("name and symbol required");
  if (!registryAddress) throw new Error("registryAddress required");

  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deploying subnet "${name}" (${symbol}) with: ${deployer.address}`);

  let nonce = await deployer.getNonce();
  const send = (opts) => ({ ...opts, nonce: nonce++ });

  // 1. Deploy Subnet ERC20
  const supply = hre.ethers.parseEther(initialSupply);
  const Subnet = await hre.ethers.getContractFactory("Subnet");
  const subnet = await Subnet.deploy(name, symbol, supply, send({}));
  await subnet.waitForDeployment();
  const subnetAddress = await subnet.getAddress();
  console.log(`Subnet token deployed: ${subnetAddress}`);

  // 2. Deploy Staking
  const Staking = await hre.ethers.getContractFactory("Staking");
  const staking = await Staking.deploy(
    subnetAddress,
    maxLockBlocks,
    maxStakersPerValidator,
    defaultCommissionBps,
    send({})
  );
  await staking.waitForDeployment();
  const stakingAddress = await staking.getAddress();
  console.log(`Staking deployed: ${stakingAddress}`);

  // 3. Deploy ConsensusYuma
  const emRate = hre.ethers.parseEther(emissionRate);
  const ConsensusYuma = await hre.ethers.getContractFactory("ConsensusYuma");
  const consensus = await ConsensusYuma.deploy(
    subnetAddress,
    stakingAddress,
    emRate,
    decayBps,
    epochLength,
    send({})
  );
  await consensus.waitForDeployment();
  const consensusAddress = await consensus.getAddress();
  console.log(`ConsensusYuma deployed: ${consensusAddress}`);

  // 4. Set ConsensusYuma as minter
  await (await subnet.setMinter(consensusAddress, send({}))).wait();
  console.log(`Minter set to ConsensusYuma`);

  // 5. Register in Registry
  const Registry = await hre.ethers.getContractFactory("Registry");
  const registry = Registry.attach(registryAddress);

  // Approve governance token
  const govTokenAddr = await registry.governanceToken();
  const GovToken = await hre.ethers.getContractFactory("Subnet");
  const govToken = GovToken.attach(govTokenAddr);

  const cost = await registry.registrationCost();
  if (cost > 0n) {
    await (await govToken.approve(registryAddress, cost, send({}))).wait();
    console.log(`Approved ${hre.ethers.formatEther(cost)} GOV for registration`);
  }

  const tx = await registry.registerSubnet(name, subnetAddress, stakingAddress, consensusAddress, send({}));
  const receipt = await tx.wait();
  console.log(`Subnet registered in Registry (tx: ${receipt.hash})`);

  // Output result as JSON for the API to parse
  const result = {
    subnet: subnetAddress,
    staking: stakingAddress,
    consensus: consensusAddress,
    name,
    symbol,
    initialSupply,
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
