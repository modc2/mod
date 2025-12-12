const hre = require('hardhat');

async function main() {
  console.log('Deploying ZCon Privacy Coin contracts...');

  const ZconPrivacyToken = await hre.ethers.getContractFactory('ZconPrivacyToken');
  const zconToken = await ZconPrivacyToken.deploy();
  await zconToken.waitForDeployment();
  console.log('ZconPrivacyToken deployed to:', await zconToken.getAddress());

  const ZconOracle = await hre.ethers.getContractFactory('ZconOracle');
  const zconOracle = await ZconOracle.deploy();
  await zconOracle.waitForDeployment();
  console.log('ZconOracle deployed to:', await zconOracle.getAddress());

  console.log('\nDeployment complete!');
  console.log('ZconPrivacyToken:', await zconToken.getAddress());
  console.log('ZconOracle:', await zconOracle.getAddress());
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
