const hre = require('hardhat');

async function main() {
  console.log('🎮 BlocTime Protocol Interaction Script');

  const [deployer, user1, user2] = await hre.ethers.getSigners();
  console.log('Using account:', deployer.address);

  // Load contract addresses from environment or use defaults
  const NATIVE_TOKEN = process.env.NATIVE_TOKEN_ADDRESS;
  const STAKING = process.env.STAKING_ADDRESS;
  const REGISTRY = process.env.REGISTRY_ADDRESS;
  const MARKETPLACE = process.env.MARKETPLACE_ADDRESS;
  const INTEGRATION = process.env.INTEGRATION_ADDRESS;

  if (!NATIVE_TOKEN || !STAKING || !REGISTRY || !MARKETPLACE || !INTEGRATION) {
    console.error('❌ Please set contract addresses in environment variables');
    process.exit(1);
  }

  // Load contracts
  const nativeToken = await hre.ethers.getContractAt('MockERC20', NATIVE_TOKEN);
  const staking = await hre.ethers.getContractAt('BlocTimeStaking', STAKING);
  const registry = await hre.ethers.getContractAt('BlocTimeRegistry', REGISTRY);
  const marketplace = await hre.ethers.getContractAt('BlocTimeMarketplaceV3', MARKETPLACE);
  const integration = await hre.ethers.getContractAt('BlocTimeIntegration', INTEGRATION);

  console.log('\n📊 System Health Check...');
  const [marketplaceHealthy, registryHealthy, stakingHealthy, status] = await integration.healthCheck();
  console.log('Marketplace:', marketplaceHealthy ? '✅' : '❌');
  console.log('Registry:', registryHealthy ? '✅' : '❌');
  console.log('Staking:', stakingHealthy ? '✅' : '❌');
  console.log('Status:', status);

  console.log('\n📈 System Statistics...');
  const [totalModules, totalRentals, totalStaked, totalBlocTime, treasuryBalance] = 
    await integration.getSystemStats();
  console.log('Total Modules:', totalModules.toString());
  console.log('Total Rentals:', totalRentals.toString());
  console.log('Total Staked:', hre.ethers.formatEther(totalStaked));
  console.log('Total BlocTime:', hre.ethers.formatEther(totalBlocTime));
  console.log('Treasury Balance:', hre.ethers.formatEther(treasuryBalance));

  // Example: Register a module
  console.log('\n📦 Registering a test module...');
  const pricePerBlock = hre.ethers.parseEther('0.01');
  const maxUsers = 10;
  const ipfsHash = 'QmTestModule123';
  
  const tx1 = await registry.registerModule(pricePerBlock, maxUsers, ipfsHash);
  await tx1.wait();
  console.log('✅ Module registered');

  // Example: Stake tokens
  console.log('\n💰 Staking tokens...');
  const stakeAmount = hre.ethers.parseEther('1000');
  const lockBlocks = 50000;
  
  await nativeToken.approve(STAKING, stakeAmount);
  const tx2 = await staking.stake(stakeAmount, lockBlocks);
  await tx2.wait();
  console.log('✅ Tokens staked');

  const [amount, startBlock, locks, blocTime, remaining, rewards] = 
    await staking.getStakeInfo(deployer.address);
  console.log('Staked Amount:', hre.ethers.formatEther(amount));
  console.log('BlocTime Earned:', hre.ethers.formatEther(blocTime));
  console.log('Blocks Remaining:', remaining.toString());
  console.log('Pending Rewards:', hre.ethers.formatEther(rewards));

  // Example: Rent a module
  console.log('\n🏪 Renting module...');
  const blocks = 1000;
  const cost = pricePerBlock * BigInt(blocks);
  
  await nativeToken.approve(MARKETPLACE, cost);
  const tx3 = await marketplace.rent(1, blocks);
  const receipt = await tx3.wait();
  console.log('✅ Module rented');

  console.log('\n✅ Interaction complete!');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
