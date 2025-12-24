const hre = require("hardhat");

async function main() {
  console.log("Deploying Newma contract to Base...");
  
  const Newma = await hre.ethers.getContractFactory("Newma");
  const newma = await Newma.deploy();
  await newma.waitForDeployment();
  
  const address = await newma.getAddress();
  console.log(`Newma deployed to: ${address}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
