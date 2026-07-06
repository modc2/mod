const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function sendAndConfirm(txPromise, label = "Transaction") {
  const tx = await txPromise;
  console.log(`${label} tx sent: ${tx.hash}`);
  const receipt = await tx.wait(1);
  console.log(`${label} confirmed in block ${receipt.blockNumber}`);
  await new Promise(resolve => setTimeout(resolve, 2000));
  return receipt;
}

async function main() {
  console.log("Deploying Registry...");

  const [deployer] = await hre.ethers.getSigners();
  console.log("Deployer:", deployer.address);
  console.log("Network:", hre.network.name);

  // Deploy Registry
  const Registry = await hre.ethers.getContractFactory("Registry");
  const registryDeployTx = await Registry.deploy();
  await sendAndConfirm(registryDeployTx.deploymentTransaction(), "Registry deploy");
  const registryAddress = await registryDeployTx.getAddress();
  console.log("Registry deployed to:", registryAddress);

  // Save to registry config.json
  const chainId = (await hre.ethers.provider.getNetwork()).chainId.toString();
  const networkName = hre.network.name;
  const registryConfigPath = path.join(__dirname, "..", "..", "..", "registry", "config.json");

  let config = {};
  if (fs.existsSync(registryConfigPath)) {
    try {
      config = JSON.parse(fs.readFileSync(registryConfigPath, "utf8"));
    } catch (e) {
      console.warn("Could not parse config.json, creating new");
    }
  }

  // Ensure evm section exists
  if (!config.evm) config.evm = {};

  config.evm[networkName] = {
    rpc: hre.network.config.url || "",
    chain_id: parseInt(chainId),
    registry: registryAddress,
    deployer: deployer.address,
    deployed_at: new Date().toISOString(),
  };

  fs.writeFileSync(registryConfigPath, JSON.stringify(config, null, 2));
  console.log("Saved to config.json");

  console.log("\nDeployment Summary:");
  console.log("  Network:", networkName);
  console.log("  Chain ID:", chainId);
  console.log("  Registry:", registryAddress);
  console.log("  Deployer:", deployer.address);
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
