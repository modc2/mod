const hre = require('hardhat');

/**
 * @title OpenHouse Deployment Script
 * @notice Deploy with the confidence of a champion
 * @dev Built to be bulletproof, auditable, and unstoppable
 */

async function main() {
  console.log('\n🏗️  DEPLOYING OPENHOUSE - FORTRESS MODE ACTIVATED\n');
  
  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying with account:', deployer.address);
  console.log('Account balance:', (await deployer.getBalance()).toString());
  
  // Configuration - Modify these for your deployment
  const AUTHORITY_ADDRESS = process.env.AUTHORITY_ADDRESS || deployer.address;
  const PROPERTY_DETAILS = process.env.PROPERTY_DETAILS || 'Premium Real Estate Asset - Blockchain Secured';
  const TOTAL_SHARES = process.env.TOTAL_SHARES || 1000;
  const SHARE_PRICE = hre.ethers.utils.parseEther(process.env.SHARE_PRICE || '0.1');
  
  console.log('\n📋 Deployment Configuration:');
  console.log('Authority:', AUTHORITY_ADDRESS);
  console.log('Property:', PROPERTY_DETAILS);
  console.log('Total Shares:', TOTAL_SHARES);
  console.log('Share Price:', hre.ethers.utils.formatEther(SHARE_PRICE), 'ETH');
  
  // Deploy contract
  console.log('\n🚀 Deploying OpenHouse contract...');
  const OpenHouse = await hre.ethers.getContractFactory('OpenHouse');
  const openhouse = await OpenHouse.deploy(
    AUTHORITY_ADDRESS,
    PROPERTY_DETAILS,
    TOTAL_SHARES,
    SHARE_PRICE
  );
  
  await openhouse.deployed();
  
  console.log('\n✅ OpenHouse deployed to:', openhouse.address);
  console.log('Transaction hash:', openhouse.deployTransaction.hash);
  
  // Verify deployment
  console.log('\n🔍 Verifying deployment...');
  const authority = await openhouse.authority();
  const totalShares = await openhouse.totalShares();
  const sharePrice = await openhouse.sharePrice();
  const isActive = await openhouse.isActive();
  
  console.log('Authority verified:', authority === AUTHORITY_ADDRESS ? '✅' : '❌');
  console.log('Total shares verified:', totalShares.toString() === TOTAL_SHARES.toString() ? '✅' : '❌');
  console.log('Share price verified:', sharePrice.toString() === SHARE_PRICE.toString() ? '✅' : '❌');
  console.log('Contract active:', isActive ? '✅' : '❌');
  
  // Save deployment info
  const deploymentInfo = {
    network: hre.network.name,
    contract: openhouse.address,
    authority: AUTHORITY_ADDRESS,
    propertyDetails: PROPERTY_DETAILS,
    totalShares: TOTAL_SHARES.toString(),
    sharePrice: hre.ethers.utils.formatEther(SHARE_PRICE),
    deployer: deployer.address,
    timestamp: new Date().toISOString(),
    blockNumber: openhouse.deployTransaction.blockNumber,
  };
  
  console.log('\n📄 Deployment Info:');
  console.log(JSON.stringify(deploymentInfo, null, 2));
  
  // Etherscan verification instructions
  if (hre.network.name !== 'hardhat' && hre.network.name !== 'localhost') {
    console.log('\n🔐 To verify on Etherscan, run:');
    console.log(`npx hardhat verify --network ${hre.network.name} ${openhouse.address} "${AUTHORITY_ADDRESS}" "${PROPERTY_DETAILS}" ${TOTAL_SHARES} ${SHARE_PRICE}`);
  }
  
  console.log('\n🎉 DEPLOYMENT COMPLETE - READY TO DOMINATE\n');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error('\n❌ DEPLOYMENT FAILED:');
    console.error(error);
    process.exit(1);
  });
