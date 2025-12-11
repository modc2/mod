const hre = require('hardhat');

async function main() {
  console.log('Deploying Bloc Staking System...');

  // Deploy Mock ERC20 Token
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const token = await MockERC20.deploy('Churn Token', 'CHURN');
  await token.waitForDeployment();
  const tokenAddress = await token.getAddress();
  console.log('MockERC20 deployed to:', tokenAddress);

  // Deploy BlocStaking with 100 tokens per hour price
  const pricePerHour = hre.ethers.parseEther('100');
  const BlocStaking = await hre.ethers.getContractFactory('BlocStaking');
  const blocStaking = await BlocStaking.deploy(tokenAddress, pricePerHour);
  await blocStaking.waitForDeployment();
  const blocStakingAddress = await blocStaking.getAddress();
  console.log('BlocStaking deployed to:', blocStakingAddress);

  // Get deployer
  const [deployer] = await hre.ethers.getSigners();
  console.log('Deployer address:', deployer.address);

  // Mint tokens to deployer for testing
  const mintAmount = hre.ethers.parseEther('1000000');
  await token.mint(deployer.address, mintAmount);
  console.log('Minted 1,000,000 tokens to deployer');

  console.log('\n=== Deployment Summary ===');
  console.log('Token Address:', tokenAddress);
  console.log('BlocStaking Address:', blocStakingAddress);
  console.log('Price Per Hour:', hre.ethers.formatEther(pricePerHour), 'tokens');
  console.log('Network:', hre.network.name);
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
