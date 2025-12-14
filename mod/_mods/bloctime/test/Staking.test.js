const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocTimeStaking Tests', function () {
  let owner, user1, user2, user3;
  let nativeToken, staking, blocTimeToken;
  const INITIAL_SUPPLY = ethers.parseEther('1000000');
  const MAX_LOCK_BLOCKS = 100000;
  const DISTRIBUTION_PCT = 5000;

  beforeEach(async function () {
    [owner, user1, user2, user3] = await ethers.getSigners();

    const MockERC20 = await ethers.getContractFactory('MockERC20');
    nativeToken = await MockERC20.deploy('Native Token', 'NAT', INITIAL_SUPPLY);
    await nativeToken.waitForDeployment();

    const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
    staking = await BlocTimeStaking.deploy(
      await nativeToken.getAddress(),
      'BlocTime Token',
      'BLOC',
      MAX_LOCK_BLOCKS,
      DISTRIBUTION_PCT
    );
    await staking.waitForDeployment();

    blocTimeToken = await ethers.getContractAt('BlocTimeToken', await staking.blocTimeToken());

    await nativeToken.transfer(user1.address, ethers.parseEther('10000'));
    await nativeToken.transfer(user2.address, ethers.parseEther('10000'));
    await nativeToken.transfer(user3.address, ethers.parseEther('10000'));
  });

  describe('Multiplier Points', function () {
    it('should set points correctly', async function () {
      const points = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 10000, multiplier: 15000 },
        { blocks: 50000, multiplier: 20000 },
        { blocks: 100000, multiplier: 30000 }
      ];

      await expect(staking.setPoints(points))
        .to.emit(staking, 'PointsSet')
        .withArgs(4);

      expect(await staking.getPointCount()).to.equal(4);
    });

    it('should reject non-monotonic points', async function () {
      const badPoints = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 10000, multiplier: 15000 },
        { blocks: 5000, multiplier: 20000 }
      ];

      await expect(staking.setPoints(badPoints)).to.be.revertedWith(
        'Blocks must be monotonically increasing'
      );
    });

    it('should calculate multiplier with interpolation', async function () {
      const points = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 10000, multiplier: 20000 }
      ];
      await staking.setPoints(points);

      expect(await staking.getMultiplier(0)).to.equal(10000);
      expect(await staking.getMultiplier(5000)).to.equal(15000);
      expect(await staking.getMultiplier(10000)).to.equal(20000);
    });
  });

  describe('Staking', function () {
    beforeEach(async function () {
      const points = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 50000, multiplier: 20000 },
        { blocks: 100000, multiplier: 30000 }
      ];
      await staking.setPoints(points);
    });

    it('should stake tokens and mint bloctime', async function () {
      const stakeAmount = ethers.parseEther('1000');
      const lockBlocks = 50000;

      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount);
      
      await expect(staking.connect(user1).stake(stakeAmount, lockBlocks))
        .to.emit(staking, 'Staked');

      const blocTimeBalance = await blocTimeToken.balanceOf(user1.address);
      expect(blocTimeBalance).to.equal(stakeAmount * 2n);

      const [amount, , locks, blocTime, ,] = await staking.getStakeInfo(user1.address);
      expect(amount).to.equal(stakeAmount);
      expect(locks).to.equal(lockBlocks);
      expect(blocTime).to.equal(stakeAmount * 2n);
    });

    it('should reject stake exceeding max lock blocks', async function () {
      const stakeAmount = ethers.parseEther('1000');
      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount);

      await expect(
        staking.connect(user1).stake(stakeAmount, MAX_LOCK_BLOCKS + 1)
      ).to.be.revertedWith('Exceeds max lock blocks');
    });

    it('should reject double staking', async function () {
      const stakeAmount = ethers.parseEther('1000');
      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount * 2n);
      await staking.connect(user1).stake(stakeAmount, 10000);

      await expect(
        staking.connect(user1).stake(stakeAmount, 10000)
      ).to.be.revertedWith('Already staking');
    });
  });

  describe('Unstaking', function () {
    beforeEach(async function () {
      const points = [{ blocks: 0, multiplier: 10000 }];
      await staking.setPoints(points);
    });

    it('should unstake after lock period', async function () {
      const stakeAmount = ethers.parseEther('1000');
      const lockBlocks = 10;

      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount);
      await staking.connect(user1).stake(stakeAmount, lockBlocks);

      await ethers.provider.send('hardhat_mine', [ethers.toQuantity(lockBlocks)]);

      const initialBalance = await nativeToken.balanceOf(user1.address);
      await expect(staking.connect(user1).unstake())
        .to.emit(staking, 'Unstaked');

      const finalBalance = await nativeToken.balanceOf(user1.address);
      expect(finalBalance - initialBalance).to.equal(stakeAmount);

      const blocTimeBalance = await blocTimeToken.balanceOf(user1.address);
      expect(blocTimeBalance).to.equal(0);
    });

    it('should reject unstake before lock period', async function () {
      const stakeAmount = ethers.parseEther('1000');
      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount);
      await staking.connect(user1).stake(stakeAmount, 1000);

      await expect(staking.connect(user1).unstake()).to.be.revertedWith('Still locked');
    });
  });

  describe('Treasury and Rewards', function () {
    beforeEach(async function () {
      const points = [{ blocks: 0, multiplier: 10000 }];
      await staking.setPoints(points);
    });

    it('should fund treasury', async function () {
      const fundAmount = ethers.parseEther('100');
      await nativeToken.approve(await staking.getAddress(), fundAmount);

      await expect(staking.fundTreasury(fundAmount))
        .to.emit(staking, 'TreasuryFunded')
        .withArgs(fundAmount);

      expect(await staking.treasuryBalance()).to.equal(fundAmount);
    });

    it('should calculate pending rewards correctly', async function () {
      const stakeAmount = ethers.parseEther('1000');
      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount);
      await staking.connect(user1).stake(stakeAmount, 10000);

      const fundAmount = ethers.parseEther('100');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      const pending = await staking.pendingRewards(user1.address);
      const expectedRewards = (fundAmount * BigInt(DISTRIBUTION_PCT)) / 10000n;
      expect(pending).to.equal(expectedRewards);
    });

    it('should claim rewards', async function () {
      const stakeAmount = ethers.parseEther('1000');
      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount);
      await staking.connect(user1).stake(stakeAmount, 10000);

      const fundAmount = ethers.parseEther('100');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      const initialBalance = await nativeToken.balanceOf(user1.address);
      await expect(staking.connect(user1).claimRewards())
        .to.emit(staking, 'RewardsClaimed');

      const finalBalance = await nativeToken.balanceOf(user1.address);
      expect(finalBalance).to.be.gt(initialBalance);
    });

    it('should distribute rewards proportionally', async function () {
      const stakeAmount1 = ethers.parseEther('1000');
      const stakeAmount2 = ethers.parseEther('3000');

      await nativeToken.connect(user1).approve(await staking.getAddress(), stakeAmount1);
      await staking.connect(user1).stake(stakeAmount1, 10000);

      await nativeToken.connect(user2).approve(await staking.getAddress(), stakeAmount2);
      await staking.connect(user2).stake(stakeAmount2, 10000);

      const fundAmount = ethers.parseEther('100');
      await nativeToken.approve(await staking.getAddress(), fundAmount);
      await staking.fundTreasury(fundAmount);

      const pending1 = await staking.pendingRewards(user1.address);
      const pending2 = await staking.pendingRewards(user2.address);

      expect(pending2).to.equal(pending1 * 3n);
    });
  });
});
