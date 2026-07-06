const { expect } = require("chai");
const { ethers } = require("hardhat");
const { mine } = require("@nomicfoundation/hardhat-network-helpers");

describe("ConsensusYuma", function () {
  let staking, consensus, subnet;
  let owner, user1, user2;

  const EMISSION_RATE = ethers.parseEther("100");
  const DECAY_BPS = 500;
  const EPOCH_LENGTH = 50;
  const MAX_LOCK_BLOCKS = 100000;
  const MAX_STAKERS = 3;
  const DEFAULT_COMMISSION_BPS = 1000;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const ModToken = await ethers.getContractFactory("Mod");
    subnet = await ModToken.deploy("TestNet", "TST");
    await subnet.waitForDeployment();

    // Mint tokens for testing
    await subnet.setMinter(owner.address);
    await subnet.mint(owner.address, ethers.parseEther("1000000"));

    const StakeTime = await ethers.getContractFactory("StakeTime");
    staking = await StakeTime.deploy(
      await subnet.getAddress(),
      MAX_LOCK_BLOCKS,
      MAX_STAKERS,
      DEFAULT_COMMISSION_BPS
    );
    await staking.waitForDeployment();

    const ConsensusYuma = await ethers.getContractFactory("ConsensusYuma");
    consensus = await ConsensusYuma.deploy(
      await subnet.getAddress(),
      await staking.getAddress(),
      EMISSION_RATE,
      DECAY_BPS,
      EPOCH_LENGTH
    );
    await consensus.waitForDeployment();

    // Register validators BEFORE transferring ownership
    await staking.registerValidatorAdmin("val1", 1, 1000);

    // Transfer Staking ownership to consensus (so it can advanceEpoch)
    await staking.transferOwnership(await consensus.getAddress());

    // Set consensus as minter on subnet token
    await subnet.setMinter(await consensus.getAddress());

    // Fund users with subnet tokens for staking
    await subnet.transfer(user1.address, ethers.parseEther("10000"));
    await subnet.transfer(user2.address, ethers.parseEther("10000"));

    // Approve Staking
    await subnet.connect(user1).approve(await staking.getAddress(), ethers.MaxUint256);
    await subnet.connect(user2).approve(await staking.getAddress(), ethers.MaxUint256);
  });

  describe("Deployment", function () {
    it("sets consensus params", async function () {
      const block = await consensus.getBlock();
      expect(block[3]).to.equal(EMISSION_RATE);
      expect(block[4]).to.equal(EPOCH_LENGTH);
    });

    it("references Staking contract", async function () {
      expect(await consensus.staking()).to.equal(await staking.getAddress());
    });

    it("references Mod token", async function () {
      expect(await consensus.mod_token()).to.equal(await subnet.getAddress());
    });

    it("sets decay bps", async function () {
      expect(await consensus.decayBps()).to.equal(DECAY_BPS);
    });
  });

  describe("Checkin & Scoring", function () {
    it("checkin increments blocktimeScore", async function () {
      await consensus.batchCheckin(["val1"]);
      const s = await consensus.getValidatorScore(
        ethers.keccak256(ethers.toUtf8Bytes("val1"))
      );
      expect(s.blocktimeScore).to.be.gt(0);
    });

    it("batch checkin works for multiple validators", async function () {
      await staking.connect(user1).registerValidator("val2", 0);
      await consensus.batchCheckin(["val1", "val2"]);

      const s1 = await consensus.getValidatorScore(
        ethers.keccak256(ethers.toUtf8Bytes("val1"))
      );
      const s2 = await consensus.getValidatorScore(
        ethers.keccak256(ethers.toUtf8Bytes("val2"))
      );
      expect(s1.blocktimeScore).to.be.gt(0);
      expect(s2.blocktimeScore).to.be.gt(0);
    });

    it("updates totalBlocktime", async function () {
      await consensus.batchCheckin(["val1"]);
      const block = await consensus.getBlock();
      expect(block[2]).to.be.gt(0);
    });
  });

  describe("Emission Distribution (Mint)", function () {
    it("distributes 100% to validator when no stakers", async function () {
      await consensus.batchCheckin(["val1"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const vBal = await consensus.getValidatorBalance("val1");
      expect(vBal).to.be.gt(0);
    });

    it("mints new tokens for emissions (inflationary)", async function () {
      await consensus.batchCheckin(["val1"]);
      const supplyBefore = await subnet.totalSupply();

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const supplyAfter = await subnet.totalSupply();
      expect(supplyAfter).to.be.gt(supplyBefore);
    });

    it("splits emissions between validator commission and stakers", async function () {
      await consensus.batchCheckin(["val1"]);
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const vBal = await consensus.getValidatorBalance("val1");
      const sReward = await consensus.getStakerRewards(user1.address);

      expect(vBal).to.be.gt(0);
      expect(sReward).to.be.gt(0);
      expect(sReward).to.be.gt(vBal * BigInt(7));
    });

    it("distributes to multiple stakers proportionally", async function () {
      await consensus.batchCheckin(["val1"]);

      await staking.connect(user1).stakeOn("val1", ethers.parseEther("3000"), 0);
      await staking.connect(user2).stakeOn("val1", ethers.parseEther("1000"), 0);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const r1 = await consensus.getStakerRewards(user1.address);
      const r2 = await consensus.getStakerRewards(user2.address);

      expect(r1).to.be.gt(0);
      expect(r2).to.be.gt(0);
      expect(r1).to.be.gt(r2 * BigInt(2));
    });

    it("rejects distribution before epoch", async function () {
      await consensus.batchCheckin(["val1"]);
      await consensus.produceBlock();
      await expect(consensus.distributeEmissions())
        .to.be.revertedWith("epoch not reached");
    });
  });

  describe("Reward Claims", function () {
    beforeEach(async function () {
      await consensus.batchCheckin(["val1"]);
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("1000"), 0);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }
    });

    it("staker claims rewards", async function () {
      const reward = await consensus.getStakerRewards(user1.address);
      expect(reward).to.be.gt(0);

      const balBefore = await subnet.balanceOf(user1.address);
      await consensus.connect(user1).claimStakerRewards();
      const balAfter = await subnet.balanceOf(user1.address);

      expect(balAfter - balBefore).to.equal(reward);
      expect(await consensus.getStakerRewards(user1.address)).to.equal(0);
    });

    it("rejects claim with no rewards", async function () {
      await expect(
        consensus.connect(user2).claimStakerRewards()
      ).to.be.revertedWith("nothing to claim");
    });

    it("validator claims commission via owner (non-ECDSA)", async function () {
      const vBal = await consensus.getValidatorBalance("val1");
      expect(vBal).to.be.gt(0);

      const balBefore = await subnet.balanceOf(owner.address);
      await consensus.claimValidatorRewards("val1", owner.address);
      const balAfter = await subnet.balanceOf(owner.address);

      expect(balAfter - balBefore).to.equal(vBal);
    });
  });

  describe("Block Production", function () {
    it("produces blocks and emits event", async function () {
      await consensus.batchCheckin(["val1"]);
      await expect(consensus.produceBlock())
        .to.emit(consensus, "BlockProduced");

      const block = await consensus.getBlock();
      expect(block[0]).to.equal(1);
    });

    it("rejects block production with no active validators", async function () {
      await expect(consensus.produceBlock())
        .to.be.revertedWith("no active validators");
    });

    it("auto-distributes at epoch boundary", async function () {
      await consensus.batchCheckin(["val1"]);

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      const kh = ethers.keccak256(ethers.toUtf8Bytes("val1"));
      const s = await consensus.getValidatorScore(kh);
      expect(s.earned).to.be.gt(0);
    });
  });

  describe("Admin", function () {
    it("updates emission rate", async function () {
      await consensus.setEmissionRate(ethers.parseEther("200"));
      const block = await consensus.getBlock();
      expect(block[3]).to.equal(ethers.parseEther("200"));
    });

    it("updates decay bps", async function () {
      await consensus.setDecayBps(1000);
      expect(await consensus.decayBps()).to.equal(1000);
    });

    it("rejects non-owner admin calls", async function () {
      await expect(
        consensus.connect(user1).setEmissionRate(1)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });
  });

  describe("Leaderboard", function () {
    it("returns validators sorted by score", async function () {
      await staking.connect(user1).registerValidator("low", 0);
      await consensus.batchCheckin(["val1"]);
      await mine(5);
      await consensus.batchCheckin(["val1"]);
      await consensus.batchCheckin(["low"]);

      const [keys, scores] = await consensus.getLeaderboard(2);
      expect(scores[0]).to.be.gte(scores[1]);
    });
  });

  describe("Integration: Full Flow", function () {
    it("register → stake → checkin → produce → mint → distribute → claim", async function () {
      await staking.connect(user1).stakeOn("val1", ethers.parseEther("5000"), 0);
      expect(await staking.balanceOf(user1.address)).to.equal(ethers.parseEther("5000"));

      await consensus.batchCheckin(["val1"]);

      const supplyBefore = await subnet.totalSupply();

      for (let i = 0; i < EPOCH_LENGTH; i++) {
        await consensus.produceBlock();
      }

      // New tokens were minted
      expect(await subnet.totalSupply()).to.be.gt(supplyBefore);

      const stakerReward = await consensus.getStakerRewards(user1.address);
      const validatorReward = await consensus.getValidatorBalance("val1");
      expect(stakerReward).to.be.gt(0);
      expect(validatorReward).to.be.gt(0);

      await consensus.connect(user1).claimStakerRewards();
      await consensus.claimValidatorRewards("val1", owner.address);

      expect(await consensus.getStakerRewards(user1.address)).to.equal(0);
      expect(await consensus.getValidatorBalance("val1")).to.equal(0);

      const ids = await staking.getUserStakeIds(user1.address);
      await staking.connect(user1).unstakeFrom(ids[0]);
      expect(await staking.balanceOf(user1.address)).to.equal(0);
    });
  });
});
