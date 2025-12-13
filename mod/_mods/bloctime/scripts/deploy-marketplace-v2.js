const hre = require('hardhat');

async function main() {
  console.log('🚀 Deploying Modular BlocTime System (Registry + Marketplace)...');

  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying with:', deployer.address);

  // Deploy Mock ERC20 Token
  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const token = await MockERC20.deploy('Bloc Token', 'BLOC');
  await token.waitForDeployment();
  const tokenAddress = await token.getAddress();
  console.log('✅ Token:', tokenAddress);

  // Deploy Registry
  const Registry = await hre.ethers.getContractFactory('BlocTimeRegistry');
  const registry = await Registry.deploy();
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log('✅ Registry:', registryAddress);

  // Use deployer as staking for now (can be replaced with actual staking contract)
  const stakingAddress = deployer.address;
  const feeBps = 250; // 2.5%

  // Deploy Marketplace V2 (points to registry)
  const MarketplaceV2 = await hre.ethers.getContractFactory('BlocTimeMarketplaceV2');
  const marketplace = await MarketplaceV2.deploy(
    tokenAddress,
    stakingAddress,
    registryAddress,
    feeBps
  );
  await marketplace.waitForDeployment();
  const marketplaceAddress = await marketplace.getAddress();
  console.log('✅ Marketplace V2:', marketplaceAddress);

  // Mint tokens for testing
  const mintAmount = hre.ethers.parseEther('1000000');
  await token.mint(deployer.address, mintAmount);
  console.log('✅ Minted tokens');

  console.log('\n📋 Deployment Summary:');
  console.log('Token:', tokenAddress);
  console.log('Registry:', registryAddress);
  console.log('Marketplace V2:', marketplaceAddress);
  console.log('Fee:', feeBps / 100, '%');
  console.log('\n💡 Architecture:');
  console.log('- Registry: Manages module metadata & ownership');
  console.log('- Marketplace: Handles rentals & secondary market');
  console.log('- Marketplace points to Registry for module data');
  console.log('- Fully modular & upgradeable design');
  console.log('\n🎉 Done!');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });