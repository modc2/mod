const hre = require('hardhat');

async function main() {
  console.log('🔍 Verifying BlocTime Contracts...');

  const addresses = {
    nativeToken: process.env.NATIVE_TOKEN_ADDRESS,
    staking: process.env.STAKING_ADDRESS,
    blocTimeToken: process.env.BLOCTIME_TOKEN_ADDRESS,
    registry: process.env.REGISTRY_ADDRESS,
    marketplace: process.env.MARKETPLACE_ADDRESS,
    integration: process.env.INTEGRATION_ADDRESS
  };

  // Verify Native Token
  if (addresses.nativeToken) {
    console.log('\n📦 Verifying MockERC20...');
    try {
      await hre.run('verify:verify', {
        address: addresses.nativeToken,
        constructorArguments: [
          'Native Token',
          'NAT',
          hre.ethers.parseEther('1000000')
        ]
      });
      console.log('✅ MockERC20 verified');
    } catch (error) {
      console.log('❌ MockERC20 verification failed:', error.message);
    }
  }

  // Verify Staking
  if (addresses.staking && addresses.nativeToken) {
    console.log('\n📦 Verifying BlocTimeStaking...');
    try {
      await hre.run('verify:verify', {
        address: addresses.staking,
        constructorArguments: [
          addresses.nativeToken,
          'BlocTime Token',
          'BLOC',
          100000,
          5000
        ]
      });
      console.log('✅ BlocTimeStaking verified');
    } catch (error) {
      console.log('❌ BlocTimeStaking verification failed:', error.message);
    }
  }

  // Verify Registry
  if (addresses.registry) {
    console.log('\n📦 Verifying BlocTimeRegistry...');
    try {
      await hre.run('verify:verify', {
        address: addresses.registry,
        constructorArguments: []
      });
      console.log('✅ BlocTimeRegistry verified');
    } catch (error) {
      console.log('❌ BlocTimeRegistry verification failed:', error.message);
    }
  }

  // Verify Marketplace
  if (addresses.marketplace && addresses.nativeToken && addresses.staking && addresses.registry) {
    console.log('\n📦 Verifying BlocTimeMarketplaceV3...');
    try {
      await hre.run('verify:verify', {
        address: addresses.marketplace,
        constructorArguments: [
          addresses.nativeToken,
          addresses.staking,
          addresses.registry,
          250
        ]
      });
      console.log('✅ BlocTimeMarketplaceV3 verified');
    } catch (error) {
      console.log('❌ BlocTimeMarketplaceV3 verification failed:', error.message);
    }
  }

  // Verify Integration
  if (addresses.integration && addresses.marketplace && addresses.registry && addresses.staking) {
    console.log('\n📦 Verifying BlocTimeIntegration...');
    try {
      await hre.run('verify:verify', {
        address: addresses.integration,
        constructorArguments: [
          addresses.marketplace,
          addresses.registry,
          addresses.staking
        ]
      });
      console.log('✅ BlocTimeIntegration verified');
    } catch (error) {
      console.log('❌ BlocTimeIntegration verification failed:', error.message);
    }
  }

  console.log('\n✅ Verification complete!');
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
