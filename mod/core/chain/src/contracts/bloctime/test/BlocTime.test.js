const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("BlocTime", function () {
  let blocTime, nativeToken, owner, user1, user2;
  const INITIAL_SUPPLY = ethers.parseEther("1000000");
  const MAX_LOCK_BLOCKS = 1000000;
  const DISTRIBUTION_PERCENTAGE = 10000;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Token = await ethers.getContractFactory("Token");
    nativeToken = await Token.deploy("Native Token", "NAT", INITIAL_SUPPLY);
    await nativeToken.waitForDeployment();

    const BlocTime = await ethers.getContractFactory("BlocTime");
    blocTime = await BlocTime.deploy(
      await nativeToken.getAddress(),
      "BlocTime",
      "BLOC",
      MAX_LOCK_BLOCKS,
      DISTRIBUTION_PERCENTAGE
    );
    await blocTime.waitForDeployment();

    await nativeToken.transfer(user1.address, ethers.parseEther("10000"));
    await nativeToken.transfer(user2.address, ethers.parseEther("10000"));
  });

  describe("Deployment", function () {
    it("Should set correct native token", async function () {
      expect(await blocTime.nativeToken()).to.equal(await nativeToken.getAddress());
    });

    it("Should set correct params", async function () {
      const params = await blocTime.params();
      expect(params.maxLockBlocks).to.equal(MAX_LOCK_BLOCKS);
      expect(params.distributionPercentage).to.equal(DISTRIBUTION_PERCENTAGE);
    });

    it("Should have default point", async function () {
      const point = await blocTime.points(0);
      expect(point.blocks).to.equal(0);
      expect(point.multiplier).to.equal(10000);
    });
  });

  describe("Points Management", function () {
    it("Should set points correctly", async function () {
      const points = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 100000, multiplier: 15000 },
        { blocks: 500000, multiplier: 20000 }
      ];

      await blocTime.setPoints(points);

      const point0 = await blocTime.points(0);
      expect(point0.blocks).to.equal(0);
      expect(point0.multiplier).to.equal(10000);

      const point1 = await blocTime.points(1);
      expect(point1.blocks).to.equal(100000);
      expect(point1.multiplier).to.equal(15000);
    });

    it("Should reject non-monotonic points", async function () {
      const points = [
        { blocks: 100000, multiplier: 15000 },
        { blocks: 50000, multiplier: 10000 }
      ];

      await expect(blocTime.setPoints(points)).to.be.revertedWith("Blocks must be monotonically increasing");
    });
  });

  describe("Staking", function () {
    const stakeAmount = ethers.parseEther("100");
    const lockBlocks = 100000;

    beforeEach(async function () {
      await nativeToken.connect(user1).approve(await blocTime.getAddress(), stakeAmount);
    });

    it("Should stake tokens correctly", async function () {
      await expect(blocTime.connect(user1).stake(stakeAmount, lockBlocks))
        .to.emit(blocTime, "Staked");

      const stakeIds = await blocTime.getUserStakeIds(user1.address);
      expect(stakeIds.length).to.equal(1);

      const position = await blocTime.getStakePosition(user1.address, stakeIds[0]);
      expect(position.amount).to.equal(stakeAmount);
      expect(position.lockBlocks).to.equal(lockBlocks);
    });

    it("Should mint correct blocTime tokens", async function () {
      await blocTime.connect(user1).stake(stakeAmount, lockBlocks);

      const multiplier = await blocTime.getMultiplier(lockBlocks);
      const expectedBlocTime = (stakeAmount * multiplier) / 10000n;

      expect(await blocTime.balanceOf(user1.address)).to.equal(expectedBlocTime);
    });

    it("Should reject stake exceeding max lock blocks", async function () {
      await expect(
        blocTime.connect(user1).stake(stakeAmount, MAX_LOCK_BLOCKS + 1)
      ).to.be.revertedWith("Exceeds max lock blocks");
    });
  });

  describe("Unstaking", function () {
    const stakeAmount = ethers.parseEther("100");
    const lockBlocks = 10;

    beforeEach(async function () {
      await nativeToken.connect(user1).approve(await blocTime.getAddress(), stakeAmount);
      await blocTime.connect(user1).stake(stakeAmount, lockBlocks);
    });

    it("Should unstake after lock period", async function () {
      const stakeIds = await blocTime.getUserStakeIds(user1.address);
      const stakeId = stakeIds[0];

      await ethers.provider.send("hardhat_mine", ["0x" + lockBlocks.toString(16)]);

      await expect(blocTime.connect(user1).unstake(stakeId))
        .to.emit(blocTime, "Unstaked");

      expect(await nativeToken.balanceOf(user1.address)).to.be.gt(0);
    });

    it("Should reject unstake before lock period", async function () {
      const stakeIds = await blocTime.getUserStakeIds(user1.address);
      const stakeId = stakeIds[0];

      await expect(
        blocTime.connect(user1).unstake(stakeId)
      ).to.be.revertedWith("Still locked");
    });

    it("Should burn blocTime tokens on unstake", async function () {
      const stakeIds = await blocTime.getUserStakeIds(user1.address);
      const stakeId = stakeIds[0];
      const balanceBefore = await blocTime.balanceOf(user1.address);

      await ethers.provider.send("hardhat_mine", ["0x" + lockBlocks.toString(16)]);
      await blocTime.connect(user1).unstake(stakeId);

      expect(await blocTime.balanceOf(user1.address)).to.equal(0);
    });
  });

  describe("Multiplier Calculation", function () {
    beforeEach(async function () {
      const points = [
        { blocks: 0, multiplier: 10000 },
        { blocks: 100000, multiplier: 20000 },
        { blocks: 500000, multiplier: 30000 }
      ];
      await blocTime.setPoints(points);
    });

    it("Should return correct multiplier for exact points", async function () {
      expect(await blocTime.getMultiplier(0)).to.equal(10000);
      expect(await blocTime.getMultiplier(100000)).to.equal(20000);
      expect(await blocTime.getMultiplier(500000)).to.equal(30000);
    });

    it("Should interpolate multiplier correctly", async function () {
      const multiplier = await blocTime.getMultiplier(50000);
      expect(multiplier).to.be.gt(10000);
      expect(multiplier).to.be.lt(20000);
    });
  });
});
