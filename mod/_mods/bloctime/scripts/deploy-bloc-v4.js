const hre = require('hardhat');

async function main() {
  console.log('🚀 Deploying BlocStakingV4 with Native Token Bidding System...');

  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying with account:', deployer.address);

  // Deploy Mock Main Token
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const mainToken = await MockERC20.deploy('Main Token', 'MAIN');
  await mainToken.waitForDeployment();
  const mainTokenAddress = await mainToken.getAddress();
  console.log('✅ Main Token deployed to:', mainTokenAddress);

  // Deploy BlocStakingV4
  const BlocStakingV4 = await hre.ethers.getContractFactory('BlocStakingV4');
  const blocStaking = await BlocStakingV4.deploy(mainTokenAddress);
  await blocStaking.waitForDeployment();
  const stakingAddress = await blocStaking.getAddress();
  console.log('✅ BlocStakingV4 deployed to:', stakingAddress);

  // Mint tokens for testing
  const mintAmount = hre.ethers.parseEther('1000000');
  await mainToken.mint(deployer.address, mintAmount);
  console.log('✅ Minted', hre.ethers.formatEther(mintAmount), 'main tokens to deployer');

  console.log('\n📋 Deployment Summary:');
  console.log('Main Token Address:', mainTokenAddress);
  console.log('BlocStakingV4 Address:', stakingAddress);
  console.log('\n🎉 Deployment complete!');
  console.log('\n💡 Features:');
  console.log('- Each user has their own bloc registry');
  console.log('- Each bloc has its own paired BlocToken');
  console.log('- Users convert main token to BlocToken to bid on slots');
  console.log('- Creators set slot prices in main token terms');
  console.log('- Creators control max concurrent users (can only reduce or increase to initial max)');
  console.log('- All pricing done with respect to main token');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });