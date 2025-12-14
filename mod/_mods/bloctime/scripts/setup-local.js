const hre = require('hardhat');

async function main() {
  console.log('🚀 Setting up local BlocTime environment...');

  const [deployer, alice, bob, charlie] = await hre.ethers.getSigners();
  console.log('Deployer:', deployer.address);
  console.log('Alice:', alice.address);
  console.log('Bob:', bob.address);
  console.log('Charlie:', charlie.address);

  // Deploy contracts
  console.log('\n📦 Deploying contracts...');
  
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const nativeToken = await MockERC20.deploy(
    'Native Token',
    'NAT',
    hre.ethers.parseEther('1000000')
  );
  await nativeToken.waitForDeployment();
  console.log('Native Token:', await nativeToken.getAddress());

  const BlocTimeStaking = await hre.ethers.getContractFactory('BlocTimeStaking');
  const staking = await BlocTimeStaking.deploy(
    await nativeToken.getAddress(),
    'BlocTime Token',
    'BLOC',
    100000,
    5000
  );
  await staking.waitForDeployment();
  console.log('Staking:', await staking.getAddress());

  const points = [
    { blocks: 0, multiplier: 10000 },
    { blocks: 10000, multiplier: 15000 },
    { blocks: 50000, multiplier: 20000 },
    { blocks: 100000, multiplier: 30000 }
  ];
  await staking.setPoints(points);

  const BlocTimeRegistry = await hre.ethers.getContractFactory('BlocTimeRegistry');
  const registry = await BlocTimeRegistry.deploy();
  await registry.waitForDeployment();
  console.log('Registry:', await registry.getAddress());

  const BlocTimeMarketplaceV3 = await hre.ethers.getContractFactory('BlocTimeMarketplaceV3');
  const marketplace = await BlocTimeMarketplaceV3.deploy(
    await nativeToken.getAddress(),
    await staking.getAddress(),
    await registry.getAddress(),
    250
  );
  await marketplace.waitForDeployment();
  console.log('Marketplace:', await marketplace.getAddress());

  const BlocTimeIntegration = await hre.ethers.getContractFactory('BlocTimeIntegration');
  const integration = await BlocTimeIntegration.deploy(
    await marketplace.getAddress(),
    await registry.getAddress(),
    await staking.getAddress()
  );
  await integration.waitForDeployment();
  console.log('Integration:', await integration.getAddress());

  // Setup test data
  console.log('\n🎭 Setting up test data...');
  
  // Distribute tokens
  await nativeToken.transfer(alice.address, hre.ethers.parseEther('10000'));
  await nativeToken.transfer(bob.address, hre.ethers.parseEther('10000'));
  await nativeToken.transfer(charlie.address, hre.ethers.parseEther('10000'));
  console.log('✅ Tokens distributed');

  // Alice stakes
  await nativeToken.connect(alice).approve(await staking.getAddress(), hre.ethers.parseEther('5000'));
  await staking.connect(alice).stake(hre.ethers.parseEther('5000'), 50000);
  console.log('✅ Alice staked 5000 tokens');

  // Bob registers modules
  await registry.connect(bob).registerModule(hre.ethers.parseEther('0.01'), 10, 'QmModule1');
  await registry.connect(bob).registerModule(hre.ethers.parseEther('0.02'), 5, 'QmModule2');
  console.log('✅ Bob registered 2 modules');

  // Charlie rents a module
  const cost = hre.ethers.parseEther('0.01') * 1000n;
  await nativeToken.connect(charlie).approve(await marketplace.getAddress(), cost);
  await marketplace.connect(charlie).rent(1, 1000);
  console.log('✅ Charlie rented module 1');

  // Health check
  console.log('\n🏥 Health Check...');
  const [marketplaceHealthy, registryHealthy, stakingHealthy, status] = await integration.healthCheck();
  console.log('Marketplace:', marketplaceHealthy ? '✅' : '❌');
  console.log('Registry:', registryHealthy ? '✅' : '❌');
  console.log('Staking:', stakingHealthy ? '✅' : '❌');
  console.log('Status:', status);

  // System stats
  console.log('\n📊 System Statistics...');
  const [totalModules, totalRentals, totalStaked, totalBlocTime, treasuryBalance] = 
    await integration.getSystemStats();
  console.log('Total Modules:', totalModules.toString());
  console.log('Total Rentals:', totalRentals.toString());
  console.log('Total Staked:', hre.ethers.formatEther(totalStaked));
  console.log('Total BlocTime:', hre.ethers.formatEther(totalBlocTime));
  console.log('Treasury Balance:', hre.ethers.formatEther(treasuryBalance));

  console.log('\n✅ Local environment setup complete!');
  console.log('\n📝 Contract Addresses:');
  console.log('NATIVE_TOKEN_ADDRESS=' + await nativeToken.getAddress());
  console.log('STAKING_ADDRESS=' + await staking.getAddress());
  console.log('REGISTRY_ADDRESS=' + await registry.getAddress());
  console.log('MARKETPLACE_ADDRESS=' + await marketplace.getAddress());
  console.log('INTEGRATION_ADDRESS=' + await integration.getAddress());
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
