const { expect } = require('chai');
const { ethers } = require('hardhat');
const { mine } = require('@nomicfoundation/hardhat-network-helpers');

describe('BlocTimeStaking', function () {
  let nativeToken, staking, blocTimeToken;
  let owner, user1, user2, user3;
  const INITIAL_SUPPLY = ethers.parseEther('10000000');
  const STAKE_AMOUNT = ethers.parseEther('1000');
  const MAX_LOCK_BLOCKS = 1000;
  const DISTRIBUTION_PERCENTAGE = 5000; // 50%

  beforeEach(async function () {
    [owner, user1, user2, user3] = await ethers.getSigners();

    // Deploy native token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    nativeToken = await MockERC20.deploy('Native Token', 'NATIVE');
    await nativeToken.waitForDeployment();

    // Deploy staking contract
    const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
    staking = await BlocTimeStaking.deploy(
      await nativeToken.getAddress(),
      'BlocTime Token',
      'BLOCTIME',
      MAX_LOCK_BLOCKS,
      DISTRIBUTION_PERCENTAGE
    );
    await staking.waitForDeployment();

    // Get bloctime token
    const blocTimeAddress = await staking.blocTimeToken();
    blocTimeToken = await ethers.getContractAt('BlocTimeToken', blocTimeAddress);

    // Distribute tokens
    await nativeToken.mint(owner.address, INITIAL_SUPPLY);
    await nativeToken.mint(user1.address, ethers.parseEther('100000'));
    await nativeToken.mint(user2.address, ethers.parseEther('100000'));
    await nativeToken.mint(user3.address, ethers.parseEther('100000'));
  });

  describe('Multiplier Configuration', function () {
    it('Should set multipliers for different block durations', async function () {
      await staking.setMultiplier(0, 10000); // 1x for no lock
      await staking.setMultiplier(100, 15000); // 1.5x for 100 blocks
      await staking.setMultiplier(500, 20000); // 2x for 500 blocks
      await staking.setMultiplier(1000, 30000); // 3x for 1000 blocks

      expect(await staking.getMultiplier(0)).to.equal(10000);
      expect(await staking.getMultiplier(100)).to.equal(15000);
      expect(await staking.getMultiplier(500)).to.equal(20000);
      expect(await staking.getMultiplier(1000)).to.equal(30000);
    });

    it('Should interpolate multipliers for intermediate values', async function () {
      await staking.setMultiplier(0, 10000); // 1x
      await staking.setMultiplier(1000, 30000); // 3x

      const multiplier500 = await staking.getMultiplier(500);
      expect(multiplier500).to.equal(20000); // Should be 2x (midpoint)
    });

    it('Should reject multipliers below 1x', async function () {
      await expect(
        staking.setMultiplier(100, 5000)
      ).to.be.revertedWith('Multiplier must be >= 1x');
    });
  });

  describe('Staking', function () {
    beforeEach(async function () {
      await staking.setMultiplier(0, 10000); // 1x
      await staking.setMultiplier(100, 15000); // 1.5x
      await staking.setMultiplier(500, 20000); // 2x
      await staking.setMultiplier(1000, 30000); // 3x
    });

    it('Should stake tokens and mint bloctime based on multiplier', async function () {
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 500);

      const stakeInfo = await staking.getStakeInfo(user1.address);
      expect(stakeInfo.amount).to.equal(STAKE_AMOUNT);
      expect(stakeInfo.lockBlocks).to.equal(500);

      // Should have 2x bloctime (2000 tokens)
      const blocTimeBalance = await blocTimeToken.balanceOf(user1.address);
      expect(blocTimeBalance).to.equal(STAKE_AMOUNT * 2n);
    });

    it('Should handle multiple stakers with different lock periods', async function () {
      // User1: 1000 tokens, 100 blocks (1.5x) = 1500 bloctime
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 100);

      // User2: 1000 tokens, 1000 blocks (3x) = 3000 bloctime
      await nativeToken.connect(user2).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user2).stake(STAKE_AMOUNT, 1000);

      const user1BlocTime = await blocTimeToken.balanceOf(user1.address);
      const user2BlocTime = await blocTimeToken.balanceOf(user2.address);

      expect(user1BlocTime).to.equal(ethers.parseEther('1500'));
      expect(user2BlocTime).to.equal(ethers.parseEther('3000'));

      const totalBlocTime = await staking.totalBlocTime();
      expect(totalBlocTime).to.equal(ethers.parseEther('4500'));
    });

    it('Should prevent staking beyond max lock blocks', async function () {
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await expect(
        staking.connect(user1).stake(STAKE_AMOUNT, MAX_LOCK_BLOCKS + 1)
      ).to.be.revertedWith('Exceeds max lock blocks');
    });
  });

  describe('Unstaking', function () {
    beforeEach(async function () {
      await staking.setMultiplier(0, 10000);
      await staking.setMultiplier(100, 20000);
    });

    it('Should allow unstaking after lock period', async function () {
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 100);

      // Mine 100 blocks
      await mine(100);

      const balanceBefore = await nativeToken.balanceOf(user1.address);
      await staking.connect(user1).unstake();
      const balanceAfter = await nativeToken.balanceOf(user1.address);

      expect(balanceAfter - balanceBefore).to.equal(STAKE_AMOUNT);

      // BlocTime should be burned
      const blocTimeBalance = await blocTimeToken.balanceOf(user1.address);
      expect(blocTimeBalance).to.equal(0);
    });

    it('Should prevent unstaking before lock period', async function () {
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 100);

      await expect(
        staking.connect(user1).unstake()
      ).to.be.revertedWith('Still locked');
    });
  });

  describe('Treasury Distribution', function () {
    beforeEach(async function () {
      await staking.setMultiplier(0, 10000); // 1x
      await staking.setMultiplier(1000, 30000); // 3x

      // User1: 1000 tokens, no lock (1x) = 1000 bloctime
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 0);

      // User2: 1000 tokens, max lock (3x) = 3000 bloctime
      await nativeToken.connect(user2).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user2).stake(STAKE_AMOUNT, 1000);

      // Total bloctime: 4000
      // User1: 25% share, User2: 75% share
    });

    it('Should fund treasury', async function () {
      const fundAmount = ethers.parseEther('10000');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      const treasuryBalance = await staking.treasuryBalance();
      expect(treasuryBalance).to.equal(fundAmount);
    });

    it('Should calculate rewards proportional to bloctime', async function () {
      const fundAmount = ethers.parseEther('10000');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      // Distribution is 50% of treasury = 5000 tokens
      // User1 (25% of bloctime): 1250 tokens
      // User2 (75% of bloctime): 3750 tokens

      const user1Rewards = await staking.pendingRewards(user1.address);
      const user2Rewards = await staking.pendingRewards(user2.address);

      expect(user1Rewards).to.equal(ethers.parseEther('1250'));
      expect(user2Rewards).to.equal(ethers.parseEther('3750'));
    });

    it('Should allow claiming rewards', async function () {
      const fundAmount = ethers.parseEther('10000');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      const balanceBefore = await nativeToken.balanceOf(user2.address);
      await staking.connect(user2).claimRewards();
      const balanceAfter = await nativeToken.balanceOf(user2.address);

      expect(balanceAfter - balanceBefore).to.equal(ethers.parseEther('3750'));
    });

    it('Should update distribution percentage', async function () {
      await staking.setDistributionPercentage(7500); // 75%

      const fundAmount = ethers.parseEther('10000');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      // Distribution is now 75% of treasury = 7500 tokens
      // User2 (75% of bloctime): 5625 tokens

      const user2Rewards = await staking.pendingRewards(user2.address);
      expect(user2Rewards).to.equal(ethers.parseEther('5625'));
    });
  });

  describe('Integration Test', function () {
    it('Should complete full cycle: stake -> fund -> claim -> unstake', async function () {
      // Setup multipliers
      await staking.setMultiplier(0, 10000);
      await staking.setMultiplier(500, 25000); // 2.5x

      // User stakes with 500 block lock
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 500);

      // Check bloctime minted (2.5x)
      const blocTimeBalance = await blocTimeToken.balanceOf(user1.address);
      expect(blocTimeBalance).to.equal(ethers.parseEther('2500'));

      // Fund treasury
      const fundAmount = ethers.parseEther('20000');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      // Claim rewards (50% of 20000 = 10000)
      const rewardsBefore = await staking.pendingRewards(user1.address);
      expect(rewardsBefore).to.equal(ethers.parseEther('10000'));

      await staking.connect(user1).claimRewards();

      // Mine blocks to unlock
      await mine(500);

      // Unstake
      await staking.connect(user1).unstake();

      // Verify final state
      const finalStake = await staking.stakes(user1.address);
      expect(finalStake.amount).to.equal(0);

      const finalBlocTime = await blocTimeToken.balanceOf(user1.address);
      expect(finalBlocTime).to.equal(0);
    });
  });
});