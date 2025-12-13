const hre = require('hardhat');

async function main() {
  console.log('🚀 Deploying BlocTimeMarketplace...');

  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying with:', deployer.address);

  const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
  const token = await MockERC20.deploy('Bloc Token', 'BLOC');
  await token.waitForDeployment();
  const tokenAddress = await token.getAddress();
  console.log('✅ Token:', tokenAddress);

  const stakingAddress = deployer.address;
  const feeBps = 250;

  const Marketplace = await hre.ethers.getContractFactory('BlocTimeMarketplace');
  const marketplace = await Marketplace.deploy(tokenAddress, stakingAddress, feeBps);
  await marketplace.waitForDeployment();
  const marketplaceAddress = await marketplace.getAddress();
  console.log('✅ Marketplace:', marketplaceAddress);

  const mintAmount = hre.ethers.parseEther('1000000');
  await token.mint(deployer.address, mintAmount);
  console.log('✅ Minted tokens');

  console.log('\n📋 Summary:');
  console.log('Token:', tokenAddress);
  console.log('Marketplace:', marketplaceAddress);
  console.log('Fee:', feeBps / 100, '%');
  console.log('\n🎉 Done!');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });