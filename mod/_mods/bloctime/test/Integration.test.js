const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocTime Integration Tests - Robust System Validation', function () {
  let paymentToken, staking, blocTimeToken, registry, marketplace, integration;
  let owner, moduleOwner, staker1, staker2, renter1, renter2;
  
  const INITIAL_SUPPLY = ethers.parseEther('1000000');
  const STAKE_AMOUNT = ethers.parseEther('10000');
  const MODULE_PRICE = ethers.parseEther('10');
  const MAX_USERS = 5;
  const TREASURY_FEE_BPS = 250; // 2.5%
  const MAX_LOCK_BLOCKS = 100000;
  const DISTRIBUTION_PCT = 5000; // 50%

  beforeEach(async function () {
    [owner, moduleOwner, staker1, staker2, renter1, renter2] = await ethers.getSigners();

    // Deploy payment token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    paymentToken = await MockERC20.deploy('Payment Token', 'PAY');
    await paymentToken.waitForDeployment();

    // Deploy staking system
    const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
    staking = await BlocTimeStaking.deploy(
      await paymentToken.getAddress(),
      'BlocTime Token',
      'BLOC',
      MAX_LOCK_BLOCKS,
      DISTRIBUTION_PCT
    );
    await staking.waitForDeployment();

    blocTimeToken = await ethers.getContractAt(
      'BlocTimeToken',
      await staking.blocTimeToken()
    );

    // Deploy registry
    const BlocTimeRegistry = await ethers.getContractFactory('BlocTimeRegistry');
    registry = await BlocTimeRegistry.deploy();
    await registry.waitForDeployment();

    // Deploy marketplace
    const BlocTimeMarketplaceV3 = await ethers.getContractFactory('BlocTimeMarketplaceV3');
    marketplace = await BlocTimeMarketplaceV3.deploy(
      await paymentToken.getAddress(),
      await staking.getAddress(),
      await registry.getAddress(),
      TREASURY_FEE_BPS
    );
    await marketplace.waitForDeployment();

    // Deploy integration contract
    const BlocTimeIntegration = await ethers.getContractFactory('BlocTimeIntegration');
    integration = await BlocTimeIntegration.deploy(
      await marketplace.getAddress(),
      await registry.getAddress(),
      await staking.getAddress()
    );
    await integration.waitForDeployment();

    // Setup multiplier points
    const points = [
      { blocks: 0, multiplier: 10000 },
      { blocks: 10000, multiplier: 15000 },
      { blocks: 50000, multiplier: 20000 },
      { blocks: 100000, multiplier: 30000 }
    ];
    await staking.setPoints(points);

    // Distribute tokens
    await paymentToken.mint(moduleOwner.address, INITIAL_SUPPLY);
    await paymentToken.mint(staker1.address, INITIAL_SUPPLY);
    await paymentToken.mint(staker2.address, INITIAL_SUPPLY);
    await paymentToken.mint(renter1.address, INITIAL_SUPPLY);
    await paymentToken.mint(renter2.address, INITIAL_SUPPLY);
  });

  describe('System Health Checks', function () {
    it('Should pass comprehensive health check', async function () {
      const [marketplaceOk, registryOk, stakingOk, status] = await integration.healthCheck();
      
      expect(marketplaceOk).to.be.true;
      expect(registryOk).to.be.true;
      expect(stakingOk).to.be.true;
      expect(status).to.equal('All systems operational');
    });

    it('Should return accurate system statistics', async function () {
      // Register module
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      
      // Stake tokens
      await paymentToken.connect(staker1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker1).stake(STAKE_AMOUNT, 50000);

      const [totalModules, totalRentals, totalStaked, totalBlocTime, treasuryBalance] = 
        await integration.getSystemStats();
      
      expect(totalModules).to.equal(1);
      expect(totalStaked).to.equal(STAKE_AMOUNT);
      expect(totalBlocTime).to.equal(STAKE_AMOUNT * 2n); // 2x multiplier at 50k blocks
    });
  });

  describe('End-to-End Flow: Stake → Register → Rent → Earn', function () {
    it('Should complete full ecosystem cycle', async function () {
      // 1. Stakers stake tokens
      await paymentToken.connect(staker1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker1).stake(STAKE_AMOUNT, 50000);
      
      await paymentToken.connect(staker2).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker2).stake(STAKE_AMOUNT, 10000);

      // Verify BlocTime minting
      const blocTime1 = await blocTimeToken.balanceOf(staker1.address);
      const blocTime2 = await blocTimeToken.balanceOf(staker2.address);
      expect(blocTime1).to.equal(STAKE_AMOUNT * 2n); // 2x multiplier
      expect(blocTime2).to.equal(STAKE_AMOUNT * 15000n / 10000n); // 1.5x multiplier

      // 2. Module owner registers module
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTestModule');
      const moduleId = 1;

      // Validate module
      const [valid, reason] = await integration.validateModuleRegistration(moduleId);
      expect(valid).to.be.true;

      // 3. Renter rents bloctime
      const rentalBlocks = 100;
      const rentalCost = MODULE_PRICE * BigInt(rentalBlocks);
      
      await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
      const rentalTx = await marketplace.connect(renter1).rent(moduleId, rentalBlocks);
      await rentalTx.wait();

      // Verify treasury received fee
      const treasuryFee = (rentalCost * BigInt(TREASURY_FEE_BPS)) / 10000n;
      const treasuryBalance = await staking.treasuryBalance();
      expect(treasuryBalance).to.equal(treasuryFee);

      // 4. Stakers claim rewards
      const rewards1Before = await staking.pendingRewards(staker1.address);
      const rewards2Before = await staking.pendingRewards(staker2.address);
      
      // Staker1 should have more rewards (higher BlocTime)
      expect(rewards1Before).to.be.gt(rewards2Before);

      const balance1Before = await paymentToken.balanceOf(staker1.address);
      await staking.connect(staker1).claimRewards();
      const balance1After = await paymentToken.balanceOf(staker1.address);
      
      expect(balance1After - balance1Before).to.equal(rewards1Before);
    });
  });

  describe('Fractional Rental Flow', function () {
    let moduleId, rentalId;

    beforeEach(async function () {
      // Register module
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      moduleId = 1;

      // Rent bloctime
      const rentalBlocks = 1000;
      const rentalCost = MODULE_PRICE * BigInt(rentalBlocks);
      await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
      const tx = await marketplace.connect(renter1).rent(moduleId, rentalBlocks);
      const receipt = await tx.wait();
      rentalId = 1;
    });

    it('Should list and sell fractional rental', async function () {
      // List fractional period
      const fromBlock = 100;
      const toBlock = 500;
      const listingPrice = ethers.parseEther('100');
      
      await marketplace.connect(renter1).listFractionalForSale(
        rentalId,
        fromBlock,
        toBlock,
        listingPrice
      );

      const listingId = 1;

      // Buy listing
      await paymentToken.connect(renter2).approve(await marketplace.getAddress(), listingPrice);
      await marketplace.connect(renter2).buy(listingId);

      // Verify treasury received fee from secondary sale
      const treasuryFee = (listingPrice * BigInt(TREASURY_FEE_BPS)) / 10000n;
      const treasuryBalance = await staking.treasuryBalance();
      expect(treasuryBalance).to.be.gte(treasuryFee);

      // Verify buyer received new rental
      const newRentalId = 2;
      const [valid, reason] = await integration.validateRentalFlow(newRentalId);
      expect(valid).to.be.true;
    });

    it('Should prevent overlapping listings', async function () {
      // Create first listing
      await marketplace.connect(renter1).listFractionalForSale(
        rentalId,
        100,
        500,
        ethers.parseEther('100')
      );

      // Try to create overlapping listing
      await expect(
        marketplace.connect(renter1).listFractionalForSale(
          rentalId,
          300,
          700,
          ethers.parseEther('100')
        )
      ).to.be.revertedWith('Overlapping listing exists');
    });

    it('Should allow non-overlapping listings', async function () {
      // Create first listing
      await marketplace.connect(renter1).listFractionalForSale(
        rentalId,
        100,
        300,
        ethers.parseEther('50')
      );

      // Create non-overlapping listing
      await marketplace.connect(renter1).listFractionalForSale(
        rentalId,
        300,
        500,
        ethers.parseEther('50')
      );

      const listings = await marketplace.getRentalListings(rentalId);
      expect(listings.length).to.equal(2);
    });
  });

  describe('Multiplier Curve Validation', function () {
    it('Should enforce monotonic multiplier points', async function () {
      const invalidPoints = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 10000, multiplier: 20000 },
        { blocks: 20000, multiplier: 15000 } // Decreasing multiplier
      ];

      await expect(
        staking.setPoints(invalidPoints)
      ).to.be.revertedWith('Multiplier must be monotonically non-decreasing');
    });

    it('Should enforce monotonic block ordering', async function () {
      const invalidPoints = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 20000, multiplier: 15000 },
        { blocks: 10000, multiplier: 20000 } // Out of order
      ];

      await expect(
        staking.setPoints(invalidPoints)
      ).to.be.revertedWith('Blocks must be monotonically increasing');
    });

    it('Should correctly interpolate between points', async function () {
      // At 25000 blocks, should be halfway between 1.5x and 2.0x = 1.75x
      const multiplier = await staking.getMultiplier(25000);
      const expected = 17500; // 1.75x in basis points
      expect(multiplier).to.equal(expected);
    });
  });

  describe('Treasury Distribution Edge Cases', function () {
    it('Should handle zero treasury balance', async function () {
      await paymentToken.connect(staker1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker1).stake(STAKE_AMOUNT, 10000);

      const rewards = await staking.pendingRewards(staker1.address);
      expect(rewards).to.equal(0);
    });

    it('Should handle single staker claiming all rewards', async function () {
      // Stake
      await paymentToken.connect(staker1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker1).stake(STAKE_AMOUNT, 10000);

      // Generate treasury
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      const rentalCost = MODULE_PRICE * 100n;
      await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
      await marketplace.connect(renter1).rent(1, 100);

      // Claim all rewards
      const treasuryBefore = await staking.treasuryBalance();
      const rewards = await staking.pendingRewards(staker1.address);
      
      await staking.connect(staker1).claimRewards();
      
      const treasuryAfter = await staking.treasuryBalance();
      expect(treasuryBefore - treasuryAfter).to.equal(rewards);
    });

    it('Should distribute rewards proportionally to multiple stakers', async function () {
      // Staker1: 2x multiplier
      await paymentToken.connect(staker1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker1).stake(STAKE_AMOUNT, 50000);

      // Staker2: 1.5x multiplier
      await paymentToken.connect(staker2).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(staker2).stake(STAKE_AMOUNT, 10000);

      // Generate treasury
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      const rentalCost = MODULE_PRICE * 1000n;
      await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
      await marketplace.connect(renter1).rent(1, 1000);

      const rewards1 = await staking.pendingRewards(staker1.address);
      const rewards2 = await staking.pendingRewards(staker2.address);

      // Staker1 should have ~57% of rewards (2x / 3.5x total)
      // Staker2 should have ~43% of rewards (1.5x / 3.5x total)
      const ratio = (rewards1 * 10000n) / rewards2;
      expect(ratio).to.be.closeTo(13333n, 100n); // ~1.33 ratio
    });
  });

  describe('Access Control & Security', function () {
    it('Should prevent non-owner from setting multiplier points', async function () {
      const points = [{ blocks: 0, multiplier: 10000 }];
      await expect(
        staking.connect(staker1).setPoints(points)
      ).to.be.reverted;
    });

    it('Should prevent non-owner from updating module', async function () {
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      
      await expect(
        registry.connect(staker1).updateModule(1, MODULE_PRICE * 2n, MAX_USERS)
      ).to.be.revertedWith('Not module owner');
    });

    it('Should prevent non-renter from listing rental', async function () {
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      
      const rentalCost = MODULE_PRICE * 100n;
      await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
      await marketplace.connect(renter1).rent(1, 100);

      await expect(
        marketplace.connect(renter2).listFractionalForSale(1, 10, 50, ethers.parseEther('10'))
      ).to.be.revertedWith('Not renter');
    });
  });

  describe('Stress Tests', function () {
    it('Should handle multiple concurrent rentals', async function () {
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      
      const rentalCost = MODULE_PRICE * 100n;
      
      // Create max concurrent rentals
      for (let i = 0; i < MAX_USERS; i++) {
        await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
        await marketplace.connect(renter1).rent(1, 100);
      }

      // Verify module is full
      const available = await registry.isModuleAvailable(1);
      expect(available).to.be.false;
    });

    it('Should handle large number of listings', async function () {
      await registry.connect(moduleOwner).registerModule(MODULE_PRICE, MAX_USERS, 'QmTest');
      
      const rentalCost = MODULE_PRICE * 10000n;
      await paymentToken.connect(renter1).approve(await marketplace.getAddress(), rentalCost);
      await marketplace.connect(renter1).rent(1, 10000);

      // Create multiple non-overlapping listings
      for (let i = 0; i < 10; i++) {
        await marketplace.connect(renter1).listFractionalForSale(
          1,
          i * 1000,
          (i + 1) * 1000,
          ethers.parseEther('10')
        );
      }

      const listings = await marketplace.getRentalListings(1);
      expect(listings.length).to.equal(10);
    });
  });
});
