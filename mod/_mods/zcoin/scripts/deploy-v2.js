const hre = require('hardhat');

async function main() {
  console.log('Deploying ZCon Privacy Coin V2 contracts...');

  // Deploy Merkle Tree
  const ZconMerkleTree = await hre.ethers.getContractFactory('ZconMerkleTree');
  const merkleTree = await ZconMerkleTree.deploy();
  await merkleTree.waitForDeployment();
  console.log('ZconMerkleTree deployed to:', await merkleTree.getAddress());

  // Deploy Privacy Token V2
  const ZconPrivacyTokenV2 = await hre.ethers.getContractFactory('ZconPrivacyTokenV2');
  const zconTokenV2 = await ZconPrivacyTokenV2.deploy();
  await zconTokenV2.waitForDeployment();
  console.log('ZconPrivacyTokenV2 deployed to:', await zconTokenV2.getAddress());

  // Deploy Relayer
  const ZconRelayer = await hre.ethers.getContractFactory('ZconRelayer');
  const zconRelayer = await ZconRelayer.deploy();
  await zconRelayer.waitForDeployment();
  console.log('ZconRelayer deployed to:', await zconRelayer.getAddress());

  // Deploy Oracle
  const ZconOracle = await hre.ethers.getContractFactory('ZconOracle');
  const zconOracle = await ZconOracle.deploy();
  await zconOracle.waitForDeployment();
  console.log('ZconOracle deployed to:', await zconOracle.getAddress());

  console.log('\n=== Deployment Complete ===');
  console.log('ZconMerkleTree:', await merkleTree.getAddress());
  console.log('ZconPrivacyTokenV2:', await zconTokenV2.getAddress());
  console.log('ZconRelayer:', await zconRelayer.getAddress());
  console.log('ZconOracle:', await zconOracle.getAddress());
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });