const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("ConsensusStaked", function () {
  let stakeTime, consensus, subnet;
  let owner, user1, user2;

  const EMISSION_RATE = ethers.parseEther("100");
  const EPOCH_LENGTH = 50;
  const MAX_LOCK_BLOCKS = 100000;
  const MAX_STAKERS = 10;
  const DEFAULT_COMMISSION_BPS = 1000;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Subnet = await ethers.getContractFactory("Subnet");
    subnet = await Subnet.deploy("StakedNet", "STK", ethers.parseEther("1000000"));
    await subnet.waitForDeployment();

    const StakeTime = await ethers.getContractFactory("StakeTime");
    stakeTime = await StakeTime.deploy(
      await subnet.getAddress(),
      await subnet.getAddress(),
      MAX_LOCK_BLOCKS,
      MAX_STAKERS,
      DEFAULT_COMMISSION_BPS,
      EPOCH_LENGTH,
      EMISSION_RATE,
      0
    );
    await stakeTime.waitForDeployment();

    const ConsensusStaked = await ethers.getContractFactory("ConsensusStaked");
    consensus = await ConsensusStaked.deploy(
      await subnet.getAddress(),
      await stakeTime.getAddress(),
      EMISSION_RATE,
      EPOCH_LENGTH
    );
    await consensus.waitForDeployment();

    await stakeTime.registerValidatorAdmin("val1", 1, 1000);
    await stakeTime.transferOwnership(await consensus.getAddress());
    await subnet.setMinter(await consensus.getAddress());

    await subnet.transfer(user1.address, ethers.parseEther("10000"));
    await subnet.transfer(user2.address, ethers.parseEther("10000"));
    await subnet.connect(user1).approve(await stakeTime.getAddress(), ethers.MaxUint256);
    await subnet.connect(user2).approve(await stakeTime.getAddress(), ethers.MaxUint256);
  });

  describe("Scoring", function () {
    it("score equals STT staked on validator", async function () {
      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("5000"), 0);
      await consensus.batchCheckin(["val1"]);

      const kh = ethers.keccak256(ethers.toUtf8Bytes("val1"));
      const s = await consensus.getValidatorScore(kh);
      expect(s.blocktimeScore).to.equal(ethers.parseEther("5000"));
    });

    it("score updates when more stake is added", async function () {
      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);
      await consensus.batchCheckin(["val1"]);

      const kh = ethers.keccak256(ethers.toUtf8Bytes("val1"));
      let s = await consensus.getValidatorScore(kh);
      expect(s.blocktimeScore).to.equal(ethers.parseEther("1000"));

      await stakeTime.connect(user2).stakeOn("val1", ethers.parseEther("2000"), 0);
      await consensus.batchCheckin(["val1"]);

      s = await consensus.getValidatorScore(kh);
      expect(s.blocktimeScore).to.equal(ethers.parseEther("3000"));
    });

    it("requires checkin for liveness", async function () {
      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("5000"), 0);
      // No checkin — totalBlocktime should be 0
      const block = await consensus.getBlock();
      expect(block[2]).to.equal(0);
    });
  });

  describe("Distribution", function () {
    it("distributes proportional to STT staked", async function () {
      await stakeTime.connect(user1).registerValidator("val2", 0);

      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("3000"), 0);
      await stakeTime.connect(user2).stakeOn("val2", ethers.parseEther("1000"), 0);

      await consensus.batchCheckin(["val1", "val2"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const v1Bal = await consensus.getValidatorBalance("val1");
      const v2Bal = await consensus.getValidatorBalance("val2");

      // Both get rewards
      expect(v1Bal).to.be.gt(0);
      expect(v2Bal).to.be.gt(0);

      // val1 has 3x the stake, should get ~3x rewards
      // (using staker rewards since validator gets commission only)
      const r1 = await consensus.getStakerRewards(user1.address);
      const r2 = await consensus.getStakerRewards(user2.address);
      expect(r1).to.be.gt(r2 * BigInt(2));
    });

    it("skips validators that didn't check in", async function () {
      await stakeTime.connect(user1).registerValidator("val2", 0);

      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);
      await stakeTime.connect(user2).stakeOn("val2", ethers.parseEther("1000"), 0);

      // Only val1 checks in
      await consensus.batchCheckin(["val1"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const v1Bal = await consensus.getValidatorBalance("val1");
      const v2Bal = await consensus.getValidatorBalance("val2");

      expect(v1Bal).to.be.gt(0);
      expect(v2Bal).to.equal(0); // no checkin = no rewards
    });

    it("mints fresh tokens", async function () {
      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);
      await consensus.batchCheckin(["val1"]);

      const supplyBefore = await subnet.totalSupply();

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      expect(await subnet.totalSupply()).to.be.gt(supplyBefore);
    });
  });

  describe("Claims", function () {
    it("full flow: stake → checkin → produce → claim", async function () {
      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("5000"), 0);
      await consensus.batchCheckin(["val1"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const reward = await consensus.getStakerRewards(user1.address);
      expect(reward).to.be.gt(0);

      await consensus.connect(user1).claimStakerRewards();
      expect(await consensus.getStakerRewards(user1.address)).to.equal(0);

      const vBal = await consensus.getValidatorBalance("val1");
      expect(vBal).to.be.gt(0);
      await consensus.claimValidatorRewards("val1", owner.address);
      expect(await consensus.getValidatorBalance("val1")).to.equal(0);
    });
  });
});
