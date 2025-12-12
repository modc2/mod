const { expect } = require('chai');
const { ethers } = require('hardhat');
const { time } = require('@nomicfoundation/hardhat-network-helpers');

describe('BlocStakingV3 - Bloctime Fee Distribution', function () {
  let token, blocStaking, owner, creator, staker1, staker2, renter;
  const PRICE_PER_INTERVAL = ethers.parseEther('100');
  const INTERVAL_DURATION = 3600; // 1 hour
  const MAX_CONCURRENT = 10;
  const STAKE_AMOUNT = ethers.parseEther('1000');

  beforeEach(async function () {
    [owner, creator, staker1, staker2, renter] = await ethers.getSigners();

    // Deploy token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    token = await MockERC20.deploy('Bloc Token', 'BLOC');
    await token.waitForDeployment();

    // Deploy BlocStakingV3
    const BlocStakingV3 = await ethers.getContractFactory('BlocStakingV3');
    blocStaking = await BlocStakingV3.deploy(
      await token.getAddress(),
      PRICE_PER_INTERVAL,
      INTERVAL_DURATION,
      MAX_CONCURRENT
    );
    await blocStaking.waitForDeployment();

    // Distribute tokens
    await token.mint(creator.address, ethers.parseEther('100000'));
    await token.mint(staker1.address, ethers.parseEther('100000'));
    await token.mint(staker2.address, ethers.parseEther('100000'));
    await token.mint(renter.address, ethers.parseEther('100000'));
  });

  describe('Bloctime Calculation', function () {
    it('Should calculate bloctime with time multiplier', async function () {
      await blocStaking.connect(creator).registerBloc('QmTest');
      
      await token.connect(staker1).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(staker1).stake(0, STAKE_AMOUNT);

      // Initial bloctime (1x multiplier)
      let bloctime = await blocStaking.calculateBloctime(0, staker1.address);
      expect(bloctime).to.equal(STAKE_AMOUNT);

      // After 182.5 days (half year) - should be ~1.5x
      await time.increase(182.5 * 24 * 3600);
      bloctime = await blocStaking.calculateBloctime(0, staker1.address);
      const expected = STAKE_AMOUNT * 15000n / 10000n; // 1.5x
      expect(bloctime).to.be.closeTo(expected, ethers.parseEther('10'));

      // After 365 days - should be 2x
      await time.increase(182.5 * 24 * 3600);
      bloctime = await blocStaking.calculateBloctime(0, staker1.address);
      expect(bloctime).to.equal(STAKE_AMOUNT * 2n);
    });
  });

  describe('Fee Distribution', function () {
    beforeEach(async function () {
      await blocStaking.connect(creator).registerBloc('QmTest');
      
      // Two stakers with equal amounts
      await token.connect(staker1).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(staker1).stake(0, STAKE_AMOUNT);
      
      await token.connect(staker2).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(staker2).stake(0, STAKE_AMOUNT);
    });

    it('Should distribute fees based on bloctime', async function () {
      // Advance time for staker1 to get higher multiplier
      await time.increase(180 * 24 * 3600); // ~6 months
      
      // Renter purchases access
      const intervals = 2;
      const cost = PRICE_PER_INTERVAL * BigInt(intervals);
      await token.connect(renter).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(renter).purchaseExclusiveAccess(0, intervals);

      // Check pending rewards
      const info1 = await blocStaking.getStakerInfo(0, staker1.address);
      const info2 = await blocStaking.getStakerInfo(0, staker2.address);
      
      // Staker1 should have more rewards due to higher bloctime
      expect(info1.pendingRewards).to.be.gt(info2.pendingRewards);
    });

    it('Should allow claiming rewards', async function () {
      // Purchase access to generate fees
      const intervals = 5;
      const cost = PRICE_PER_INTERVAL * BigInt(intervals);
      await token.connect(renter).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(renter).purchaseExclusiveAccess(0, intervals);

      // Claim rewards
      const balanceBefore = await token.balanceOf(staker1.address);
      await blocStaking.connect(staker1).claimRewards(0);
      const balanceAfter = await token.balanceOf(staker1.address);

      expect(balanceAfter).to.be.gt(balanceBefore);
    });
  });

  describe('Staker Info', function () {
    it('Should return complete staker information', async function () {
      await blocStaking.connect(creator).registerBloc('QmTest');
      
      await token.connect(staker1).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(staker1).stake(0, STAKE_AMOUNT);

      await time.increase(100 * 24 * 3600); // ~100 days

      const info = await blocStaking.getStakerInfo(0, staker1.address);
      
      expect(info.amount).to.equal(STAKE_AMOUNT);
      expect(info.bloctime).to.be.gt(STAKE_AMOUNT); // Should be > 1x due to time
      expect(info.pendingRewards).to.equal(0); // No fees distributed yet
    });
  });

  describe('Integration: Stake, Rent, Earn', function () {
    it('Should complete full cycle: stake -> rent -> earn -> claim', async function () {
      // 1. Register bloc
      await blocStaking.connect(creator).registerBloc('QmTest');
      
      // 2. Stake tokens
      await token.connect(staker1).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(staker1).stake(0, STAKE_AMOUNT);
      
      // 3. Wait to build bloctime
      await time.increase(30 * 24 * 3600); // 30 days
      
      // 4. Renter purchases access (generates fees)
      const intervals = 10;
      const cost = PRICE_PER_INTERVAL * BigInt(intervals);
      await token.connect(renter).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(renter).purchaseExclusiveAccess(0, intervals);
      
      // 5. Check rewards
      const info = await blocStaking.getStakerInfo(0, staker1.address);
      expect(info.pendingRewards).to.be.gt(0);
      
      // 6. Claim rewards
      const balanceBefore = await token.balanceOf(staker1.address);
      await blocStaking.connect(staker1).claimRewards(0);
      const balanceAfter = await token.balanceOf(staker1.address);
      
      expect(balanceAfter).to.be.gt(balanceBefore);
      expect(balanceAfter - balanceBefore).to.equal(info.pendingRewards);
    });
  });
});
