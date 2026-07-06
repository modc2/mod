const { expect } = require("chai");
const { ethers } = require("hardhat");
const { mine } = require("@nomicfoundation/hardhat-network-helpers");

describe("Staking", function () {
  let staking, subnet;
  let owner, user1, user2;

  const MAX_LOCK_BLOCKS = 100000;
  const MAX_STAKERS = 3;
  const DEFAULT_COMMISSION_BPS = 1000;

  async function deployStaking(nativeToken) {
    const Staking = await ethers.getContractFactory("Staking");
    const st = await Staking.deploy(
      nativeToken, MAX_LOCK_BLOCKS, MAX_STAKERS, DEFAULT_COMMISSION_BPS
    );
    await st.waitForDeployment();
    return st;
  }

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Subnet = await ethers.getContractFactory("Subnet");
    subnet = await Subnet.deploy("TestNet", "TST", ethers.parseEther("1000000"));
    await subnet.waitForDeployment();

    staking = await deployStaking(await subnet.getAddress());

    await staking.registerValidatorAdmin("val1", 1, 1000);

    await subnet.transfer(user1.address, ethers.parseEther("10000"));
    await subnet.transfer(user2.address, ethers.parseEther("10000"));

    await subnet.connect(user1).approve(await staking.getAddress(), ethers.MaxUint256);
    await subnet.connect(user2).approve(await staking.getAddress(), ethers.MaxUint256);
  });

  describe("Deployment", function () {
    it("sets nativeToken to subnet address", async function () {
      expect(await staking.nativeToken()).to.equal(await subnet.getAddress());
    });

    it("sets staking params", async function () {
      expect(await staking.maxLockBlocks()).to.equal(MAX_LOCK_BLOCKS);
      expect(await staking.maxStakersPerValidator()).to.equal(MAX_STAKERS);
      expect(await staking.defaultCommissionBps()).to.equal(DEFAULT_COMMISSION_BPS);
    });

    it("has default 1x multiplier point", async function () {
      expect(await staking.getMultiplier(0)).to.equal(10000);
    });

    it("mints STT ERC20 with correct name/symbol", async function () {
      expect(await staking.name()).to.equal("StakeTime");
      expect(await staking.symbol()).to.equal("STT");
    });
  });

  describe("Validator Registration", function () {
    it("registered validator is active", async function () {
      const v = await staking.getValidator("val1");
      expect(v._key).to.equal("val1");
      expect(v._active).to.be.true;
      expect(v._commissionBps).to.equal(1000);
    });

    it("self-registers a validator", async function () {
      await staking.connect(user1).registerValidator("user1val", 0);
      const v = await staking.getValidator("user1val");
      expect(v._active).to.be.true;
      expect(v._commissionBps).to.equal(DEFAULT_COMMISSION_BPS);
    });

    it("counts validators", async function () {
      expect(await staking.validatorCount()).to.equal(1);
      await staking.connect(user1).registerValidator("v2", 0);
      expect(await staking.validatorCount()).to.equal(2);
    });
  });

  describe("Staking", function () {
    it("stakes on a validator and mints STT", async function () {
      const amount = ethers.parseEther("1000");
      await expect(staking.connect(user1).stakeOn("val1", amount, 0))
        .to.emit(staking, "Staked");
      expect(await staking.balanceOf(user1.address)).to.equal(amount);
    });

    it("applies multiplier curve for longer lock", async function () {
      const st2 = await deployStaking(await subnet.getAddress());
      await st2.registerValidatorAdmin("v1", 1, 1000);
      await st2.setPoints([
        { blocks: 0, multiplier: 10000 },
        { blocks: 10000, multiplier: 20000 },
      ]);

      await subnet.transfer(user1.address, ethers.parseEther("1000"));
      await subnet.connect(user1).approve(await st2.getAddress(), ethers.MaxUint256);
      await st2.connect(user1).stakeOn("v1", ethers.parseEther("1000"), 10000);
      expect(await st2.balanceOf(user1.address)).to.equal(ethers.parseEther("2000"));
    });

    it("tracks stake position", async function () {
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("500"), 100);
      const ids = await staking.getUserStakeIds(user1.address);
      expect(ids.length).to.equal(1);
      const pos = await staking.getStakePosition(ids[0]);
      expect(pos.staker).to.equal(user1.address);
      expect(pos.amount).to.equal(ethers.parseEther("500"));
    });

    it("tracks validator total stake time", async function () {
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);
      expect(await staking.getValidatorTotalStakeTime("val1")).to.equal(ethers.parseEther("1000"));
    });

    it("rejects zero amount", async function () {
      await expect(
        staking.connect(user1).stakeOn("val1", 0, 0)
      ).to.be.revertedWith("amount must be > 0");
    });

    it("rejects exceeding max lock blocks", async function () {
      await expect(
        staking.connect(user1).stakeOn("val1", ethers.parseEther("100"), MAX_LOCK_BLOCKS + 1)
      ).to.be.revertedWith("exceeds max lock blocks");
    });

    it("rejects staking on inactive validator", async function () {
      await expect(
        staking.connect(user1).stakeOn("nonexistent", ethers.parseEther("100"), 0)
      ).to.be.revertedWith("validator not active");
    });

    it("enforces max stakers per validator per epoch", async function () {
      const [, , , user3, user4] = await ethers.getSigners();
      await subnet.transfer(user3.address, ethers.parseEther("1000"));
      await subnet.transfer(user4.address, ethers.parseEther("1000"));
      await subnet.connect(user3).approve(await staking.getAddress(), ethers.MaxUint256);
      await subnet.connect(user4).approve(await staking.getAddress(), ethers.MaxUint256);

      await staking.connect(user1).stakeOn("val1", ethers.parseEther("100"), 0);
      await staking.connect(user2).stakeOn("val1", ethers.parseEther("100"), 0);
      await staking.connect(user3).stakeOn("val1", ethers.parseEther("100"), 0);

      await expect(
        staking.connect(user4).stakeOn("val1", ethers.parseEther("100"), 0)
      ).to.be.revertedWith("max stakers reached for this validator this epoch");
    });
  });

  describe("Unstaking", function () {
    it("rejects unstake while locked", async function () {
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 100);
      const ids = await staking.getUserStakeIds(user1.address);
      await expect(
        staking.connect(user1).unstakeFrom(ids[0])
      ).to.be.revertedWith("still locked");
    });

    it("unstakes after lock period", async function () {
      const amount = ethers.parseEther("1000");
      await staking.connect(user1).stakeOn("val1", amount, 10);
      const ids = await staking.getUserStakeIds(user1.address);
      await mine(15);

      const balBefore = await subnet.balanceOf(user1.address);
      await expect(staking.connect(user1).unstakeFrom(ids[0]))
        .to.emit(staking, "Unstaked");
      const balAfter = await subnet.balanceOf(user1.address);
      expect(balAfter - balBefore).to.equal(amount);
      expect(await staking.balanceOf(user1.address)).to.equal(0);
    });

    it("rejects unstake by non-owner of stake", async function () {
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);
      const ids = await staking.getUserStakeIds(user1.address);
      await expect(
        staking.connect(user2).unstakeFrom(ids[0])
      ).to.be.revertedWith("not your stake");
    });
  });

  describe("Multiplier Curve", function () {
    let freshStaking;

    beforeEach(async function () {
      freshStaking = await deployStaking(await subnet.getAddress());
    });

    it("sets custom points", async function () {
      await freshStaking.setPoints([
        { blocks: 0, multiplier: 10000 },
        { blocks: 50000, multiplier: 20000 },
        { blocks: 100000, multiplier: 30000 },
      ]);
      expect(await freshStaking.getMultiplier(0)).to.equal(10000);
      expect(await freshStaking.getMultiplier(100000)).to.equal(30000);
    });

    it("interpolates between points", async function () {
      await freshStaking.setPoints([
        { blocks: 0, multiplier: 10000 },
        { blocks: 100000, multiplier: 20000 },
      ]);
      expect(await freshStaking.getMultiplier(50000)).to.equal(15000);
    });

    it("rejects non-monotonic blocks", async function () {
      await expect(
        freshStaking.setPoints([
          { blocks: 100, multiplier: 10000 },
          { blocks: 50, multiplier: 20000 },
        ])
      ).to.be.revertedWith("blocks must increase");
    });

    it("rejects multiplier < 1x", async function () {
      await expect(
        freshStaking.setPoints([{ blocks: 0, multiplier: 5000 }])
      ).to.be.revertedWith("multiplier must be >= 1x");
    });
  });
});
