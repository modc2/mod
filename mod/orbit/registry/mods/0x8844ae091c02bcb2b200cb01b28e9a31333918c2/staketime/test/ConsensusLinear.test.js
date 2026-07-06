const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("ConsensusLinear", function () {
  let stakeTime, consensus, subnet;
  let owner, user1, user2;

  const EMISSION_RATE = ethers.parseEther("100");
  const EPOCH_LENGTH = 50;
  const MAX_LOCK_BLOCKS = 100000;
  const MAX_STAKERS = 10;
  const DEFAULT_COMMISSION_BPS = 1000;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const ModToken = await ethers.getContractFactory("Mod");
    subnet = await ModToken.deploy("LinearNet", "LNR");
    await subnet.waitForDeployment();

    // Mint tokens for testing
    await subnet.setMinter(owner.address);
    await subnet.mint(owner.address, ethers.parseEther("1000000"));

    const StakeTimeToken = await ethers.getContractFactory("StakeTime");
    stakeTime = await StakeTimeToken.deploy(
      await subnet.getAddress(),
      MAX_LOCK_BLOCKS,
      MAX_STAKERS,
      DEFAULT_COMMISSION_BPS
    );
    await stakeTime.waitForDeployment();

    const ConsensusLinear = await ethers.getContractFactory("ConsensusLinear");
    consensus = await ConsensusLinear.deploy(
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
    it("each checkin adds +1 to score", async function () {
      await consensus.batchCheckin(["val1"]);
      const s1 = await consensus.getValidatorScore(
        ethers.keccak256(ethers.toUtf8Bytes("val1"))
      );
      expect(s1.blocktimeScore).to.equal(1);

      await consensus.batchCheckin(["val1"]);
      const s2 = await consensus.getValidatorScore(
        ethers.keccak256(ethers.toUtf8Bytes("val1"))
      );
      expect(s2.blocktimeScore).to.equal(2);
    });

    it("no decay between checkins", async function () {
      await consensus.batchCheckin(["val1"]);
      await consensus.batchCheckin(["val1"]);
      await consensus.batchCheckin(["val1"]);

      const s = await consensus.getValidatorScore(
        ethers.keccak256(ethers.toUtf8Bytes("val1"))
      );
      expect(s.blocktimeScore).to.equal(3);
    });
  });

  describe("Distribution", function () {
    it("mints and distributes emissions", async function () {
      await consensus.batchCheckin(["val1"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const vBal = await consensus.getValidatorBalance("val1");
      expect(vBal).to.be.gt(0);
    });

    it("resets scores after epoch", async function () {
      await consensus.batchCheckin(["val1"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const kh = ethers.keccak256(ethers.toUtf8Bytes("val1"));
      const s = await consensus.getValidatorScore(kh);
      // Score reset to 0 after distribution + checkins during block production
      // totalBlocktime should also be 0
      const block = await consensus.getBlock();
      expect(block[2]).to.equal(0); // totalBlocktime reset
    });

    it("splits proportionally between validators by checkin count", async function () {
      await stakeTime.connect(user1).registerValidator("val2", 0);

      // val1 checks in 3 times, val2 checks in 1 time
      await consensus.batchCheckin(["val1"]);
      await consensus.batchCheckin(["val1"]);
      await consensus.batchCheckin(["val1"]);
      await consensus.batchCheckin(["val2"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const v1Bal = await consensus.getValidatorBalance("val1");
      const v2Bal = await consensus.getValidatorBalance("val2");

      expect(v1Bal).to.be.gt(0);
      expect(v2Bal).to.be.gt(0);
      // val1 should get ~3x val2
      expect(v1Bal).to.be.gt(v2Bal * BigInt(2));
    });
  });

  describe("Claims", function () {
    it("staker claims minted rewards", async function () {
      await consensus.batchCheckin(["val1"]);
      await stakeTime.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const reward = await consensus.getStakerRewards(user1.address);
      expect(reward).to.be.gt(0);

      const balBefore = await subnet.balanceOf(user1.address);
      await consensus.connect(user1).claimStakerRewards();
      const balAfter = await subnet.balanceOf(user1.address);
      expect(balAfter - balBefore).to.equal(reward);
    });
  });
});
