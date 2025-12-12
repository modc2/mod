const { expect } = require('chai');
const { ethers } = require('hardhat');
const { time } = require('@nomicfoundation/hardhat-network-helpers');

describe('ChurnStaking', function () {
  let token, staking, owner, user1, user2;
  const INITIAL_SUPPLY = ethers.parseEther('1000000');
  const STAKE_AMOUNT = ethers.parseEther('1000');
  const TREASURY_AMOUNT = ethers.parseEther('100000');

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    // Deploy token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    token = await MockERC20.deploy('Churn Token', 'CHURN');
    await token.waitForDeployment();

    // Deploy staking
    const ChurnStaking = await ethers.getContractFactory('ChurnStaking');
    staking = await ChurnStaking.deploy(await token.getAddress());
    await staking.waitForDeployment();

    // Distribute tokens
    await token.transfer(user1.address, ethers.parseEther('10000'));
    await token.transfer(user2.address, ethers.parseEther('10000'));

    // Fund treasury
    await token.approve(await staking.getAddress(), TREASURY_AMOUNT);
    await staking.fundTreasury(TREASURY_AMOUNT);
  });

  describe('Staking', function () {
    it('Should allow users to stake with multiplier', async function () {
      await token.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 15000); // 1.5x multiplier

      const stakeInfo = await staking.stakes(user1.address);
      expect(stakeInfo.amount).to.equal(STAKE_AMOUNT);
      expect(stakeInfo.multiplier).to.equal(15000);
    });

    it('Should reject invalid multipliers', async function () {
      await token.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await expect(
        staking.connect(user1).stake(STAKE_AMOUNT, 5000)
      ).to.be.revertedWith('Invalid multiplier');
    });
  });

  describe('Time Multiplier', function () {
    it('Should increase time multiplier over time', async function () {
      await token.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 10000);

      const initialMultiplier = await staking.getTimeMultiplier(user1.address);
      expect(initialMultiplier).to.equal(10000); // 1x at start

      // Fast forward 182.5 days (half year)
      await time.increase(182.5 * 24 * 60 * 60);

      const midMultiplier = await staking.getTimeMultiplier(user1.address);
      expect(midMultiplier).to.be.closeTo(15000n, 100n); // ~1.5x at halfway

      // Fast forward another 182.5 days
      await time.increase(182.5 * 24 * 60 * 60);

      const finalMultiplier = await staking.getTimeMultiplier(user1.address);
      expect(finalMultiplier).to.equal(20000); // 2x after full year
    });
  });

  describe('Rewards', function () {
    it('Should calculate stake-time tokens correctly', async function () {
      await token.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 20000); // 2x lock multiplier

      const stakeTimeTokens = await staking.getStakeTimeTokens(user1.address);
      // Initial: 1000 * 2.0 (lock) * 1.0 (time) = 2000
      expect(stakeTimeTokens).to.equal(ethers.parseEther('2000'));

      // After 1 year, time multiplier = 2x
      await time.increase(365 * 24 * 60 * 60);
      const stakeTimeTokensAfter = await staking.getStakeTimeTokens(user1.address);
      // After: 1000 * 2.0 (lock) * 2.0 (time) = 4000
      expect(stakeTimeTokensAfter).to.equal(ethers.parseEther('4000'));
    });
  });

  describe('Unstaking', function () {
    it('Should allow users to unstake and receive tokens', async function () {
      await token.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 10000);

      const balanceBefore = await token.balanceOf(user1.address);
      await staking.connect(user1).unstake();
      const balanceAfter = await token.balanceOf(user1.address);

      expect(balanceAfter - balanceBefore).to.equal(STAKE_AMOUNT);
    });
  });
});
