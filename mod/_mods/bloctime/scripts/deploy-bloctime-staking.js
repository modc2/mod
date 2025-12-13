const hre = require('hardhat');

async function main() {
  console.log('🚀 Deploying BlocTimeStaking System...');

  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying with account:', deployer.address);

  // Deploy Mock Native Token
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const nativeToken = await MockERC20.deploy('Native Token', 'NATIVE');
  await nativeToken.waitForDeployment();
  const nativeTokenAddress = await nativeToken.getAddress();
  console.log('✅ Native Token deployed to:', nativeTokenAddress);

  // Deploy BlocTimeStaking
  const maxLockBlocks = 100000; // ~2 weeks at 12s blocks
  const distributionPercentage = 5000; // 50% of treasury

  const BlocTimeStaking = await hre.ethers.getContractFactory('BlocTimeStaking');
  const staking = await BlocTimeStaking.deploy(
    nativeTokenAddress,
    'BlocTime Token',
    'BLOCTIME',
    maxLockBlocks,
    distributionPercentage
  );
  await staking.waitForDeployment();
  const stakingAddress = await staking.getAddress();
  console.log('✅ BlocTimeStaking deployed to:', stakingAddress);

  // Get BlocTime token address
  const blocTimeTokenAddress = await staking.blocTimeToken();
  console.log('✅ BlocTime Token deployed to:', blocTimeTokenAddress);

  // Setup multipliers
  console.log('\n⚙️  Setting up multipliers...');
  await staking.setMultiplier(0, 10000); // 1x for no lock
  console.log('  - 0 blocks: 1.0x');
  
  await staking.setMultiplier(10000, 15000); // 1.5x for ~1 day
  console.log('  - 10,000 blocks (~1 day): 1.5x');
  
  await staking.setMultiplier(50000, 20000); // 2x for ~1 week
  console.log('  - 50,000 blocks (~1 week): 2.0x');
  
  await staking.setMultiplier(100000, 30000); // 3x for max lock
  console.log('  - 100,000 blocks (~2 weeks): 3.0x');

  // Mint tokens for testing
  const mintAmount = hre.ethers.parseEther('10000000');
  await nativeToken.mint(deployer.address, mintAmount);
  console.log('\n✅ Minted', hre.ethers.formatEther(mintAmount), 'native tokens to deployer');

  // Fund treasury
  const treasuryAmount = hre.ethers.parseEther('1000000');
  await nativeToken.approve(stakingAddress, treasuryAmount);
  await staking.fundTreasury(treasuryAmount);
  console.log('✅ Funded treasury with', hre.ethers.formatEther(treasuryAmount), 'tokens');

  console.log('\n📋 Deployment Summary:');
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('Native Token:', nativeTokenAddress);
  console.log('BlocTimeStaking:', stakingAddress);
  console.log('BlocTime Token:', blocTimeTokenAddress);
  console.log('Max Lock Blocks:', maxLockBlocks);
  console.log('Distribution %:', distributionPercentage / 100, '%');
  console.log('Treasury Balance:', hre.ethers.formatEther(treasuryAmount), 'tokens');
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

  console.log('\n🎉 Deployment complete!');
  console.log('\n💡 Features:');
  console.log('  ✓ Stake native tokens for X blocks');
  console.log('  ✓ Mint BlocTime tokens based on lock duration multiplier');
  console.log('  ✓ Owner sets max blocks and point-wise multipliers');
  console.log('  ✓ Treasury distributes rewards proportional to BlocTime holdings');
  console.log('  ✓ Parameterized distribution percentage');
  console.log('  ✓ Modular design compatible with other contracts');
  console.log('\n📖 Usage:');
  console.log('  1. Users stake native tokens with chosen lock period');
  console.log('  2. BlocTime tokens minted = staked_amount × multiplier(lock_blocks)');
  console.log('  3. Treasury funded by owner');
  console.log('  4. Users claim rewards proportional to their BlocTime share');
  console.log('  5. After lock period, users unstake and BlocTime is burned');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });