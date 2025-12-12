const hre = require('hardhat');

async function main() {
  console.log('Deploying Churn Staking System...');

  // Deploy Mock ERC20 Token
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const token = await MockERC20.deploy('Churn Token', 'CHURN');
  await token.waitForDeployment();
  const tokenAddress = await token.getAddress();
  console.log('MockERC20 deployed to:', tokenAddress);

  // Deploy ChurnStaking
  const ChurnStaking = await hre.ethers.getContractFactory('ChurnStaking');
  const staking = await ChurnStaking.deploy(tokenAddress);
  await staking.waitForDeployment();
  const stakingAddress = await staking.getAddress();
  console.log('ChurnStaking deployed to:', stakingAddress);

  // Mint tokens to deployer
  const [deployer] = await hre.ethers.getSigners();
  console.log('Deployer address:', deployer.address);

  // Fund treasury with 100k tokens
  const fundAmount = hre.ethers.parseEther('100000');
  await token.approve(stakingAddress, fundAmount);
  await staking.fundTreasury(fundAmount);
  console.log('Treasury funded with 100,000 tokens');

  console.log('\n=== Deployment Summary ===');
  console.log('Token Address:', tokenAddress);
  console.log('Staking Address:', stakingAddress);
  console.log('Network:', hre.network.name);
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
