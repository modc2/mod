const hre = require('hardhat');

async function main() {
  console.log('🚀 Deploying BlocStakingV3 with Bloctime Fee Distribution...');

  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying with account:', deployer.address);

  // Deploy Mock Token for testing
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const token = await MockERC20.deploy('Bloc Token', 'BLOC');
  await token.waitForDeployment();
  const tokenAddress = await token.getAddress();
  console.log('✅ Mock Token deployed to:', tokenAddress);

  // Deploy BlocStakingV3
  const pricePerInterval = hre.ethers.parseEther('100'); // 100 tokens per interval
  const intervalDuration = 3600; // 1 hour in seconds
  const maxConcurrentUsers = 10; // Max 10 concurrent users

  const BlocStakingV3 = await hre.ethers.getContractFactory('BlocStakingV3');
  const blocStaking = await BlocStakingV3.deploy(
    tokenAddress,
    pricePerInterval,
    intervalDuration,
    maxConcurrentUsers
  );
  await blocStaking.waitForDeployment();
  const stakingAddress = await blocStaking.getAddress();
  console.log('✅ BlocStakingV3 deployed to:', stakingAddress);

  // Mint tokens for testing
  const mintAmount = hre.ethers.parseEther('1000000');
  await token.mint(deployer.address, mintAmount);
  console.log('✅ Minted', hre.ethers.formatEther(mintAmount), 'tokens to deployer');

  console.log('\n📋 Deployment Summary:');
  console.log('Token Address:', tokenAddress);
  console.log('BlocStakingV3 Address:', stakingAddress);
  console.log('Price per Interval:', hre.ethers.formatEther(pricePerInterval), 'tokens');
  console.log('Interval Duration:', intervalDuration, 'seconds');
  console.log('Max Concurrent Users:', maxConcurrentUsers);
  console.log('\n🎉 Deployment complete!');
  console.log('\n💡 Features:');
  console.log('- Stake tokens to blocs');
  console.log('- Earn fees based on bloctime (amount * time_multiplier)');
  console.log('- Time multiplier: 1x to 2x over 365 days');
  console.log('- Rent intervals or stake for fee share');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
