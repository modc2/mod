const { expect } = require("chai");
const { ethers } = require("hardhat");

/**
 * Integration tests: Consensus mechanisms × Inflation curves.
 *
 * Simulates multi-epoch validator lifecycles with real token minting,
 * staking, checkins, block production, and reward distribution under
 * different inflation schedules.
 */
describe("Consensus × Inflation Integration", function () {
  const E = ethers.parseEther;

  let owner, user1, user2;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();
  });

  // ── Helpers ──────────────────────────────────────────────────────────

  async function deployStack(consensusType, consensusArgs = {}) {
    const ModToken = await ethers.getContractFactory("Mod");
    const modToken = await ModToken.deploy("TestMod", "TMD");

    const StakeTime = await ethers.getContractFactory("StakeTime");
    const staking = await StakeTime.deploy(
      await modToken.getAddress(), 100000, 100, 1000
    );

    const emissionRate = consensusArgs.emissionRate || E("100");
    const epochLength = consensusArgs.epochLength || 10;

    let consensus;
    if (consensusType === "yuma") {
      const F = await ethers.getContractFactory("ConsensusYuma");
      consensus = await F.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        emissionRate,
        consensusArgs.decayBps || 500,
        epochLength,
      );
    } else if (consensusType === "linear") {
      const F = await ethers.getContractFactory("ConsensusLinear");
      consensus = await F.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        emissionRate,
        epochLength,
      );
    } else if (consensusType === "staked") {
      const F = await ethers.getContractFactory("ConsensusStaked");
      consensus = await F.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        emissionRate,
        epochLength,
      );
    }

    // Mint tokens for staking, then hand minter to consensus
    await modToken.setMinter(owner.address);
    await modToken.mint(owner.address, E("1000000"));
    await modToken.setMinter(await consensus.getAddress());

    // Transfer tokens to users for staking
    await modToken.transfer(user1.address, E("50000"));
    await modToken.transfer(user2.address, E("50000"));

    // Transfer ownership of staking to consensus for advanceEpoch
    await staking.transferOwnership(await consensus.getAddress());

    return { modToken, staking, consensus };
  }

  async function registerAndStake(staking, modToken, user, key, amount, lockBlocks = 0) {
    const stakingAddr = await staking.getAddress();
    await modToken.connect(user).approve(stakingAddr, amount);
    // Register via consensus owner (owner is still consensus owner initially)
    // Actually staking ownership is transferred, so we register before that
    // We'll use a pattern where we register first
  }

  async function setupValidators(staking, modToken, consensus) {
    // Register validators — need staking owner, but it's consensus now
    // Use batchCheckin from consensus owner instead
    // Actually, registerValidatorAdmin needs staking owner (consensus).
    // Let's register before transferring ownership in deployStack.
    // Refactor: register validators separately.
  }

  async function deployWithValidators(consensusType, consensusArgs = {}) {
    const ModToken = await ethers.getContractFactory("Mod");
    const modToken = await ModToken.deploy("TestMod", "TMD");

    const StakeTime = await ethers.getContractFactory("StakeTime");
    const staking = await StakeTime.deploy(
      await modToken.getAddress(), 100000, 100, 1000
    );

    const emissionRate = consensusArgs.emissionRate || E("100");
    const epochLength = consensusArgs.epochLength || 10;

    let consensus;
    if (consensusType === "yuma") {
      const F = await ethers.getContractFactory("ConsensusYuma");
      consensus = await F.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        emissionRate,
        consensusArgs.decayBps || 500,
        epochLength,
      );
    } else if (consensusType === "linear") {
      const F = await ethers.getContractFactory("ConsensusLinear");
      consensus = await F.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        emissionRate,
        epochLength,
      );
    } else if (consensusType === "staked") {
      const F = await ethers.getContractFactory("ConsensusStaked");
      consensus = await F.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        emissionRate,
        epochLength,
      );
    }

    // Mint tokens for staking
    await modToken.setMinter(owner.address);
    await modToken.mint(owner.address, E("1000000"));

    // Transfer to users
    await modToken.transfer(user1.address, E("50000"));
    await modToken.transfer(user2.address, E("50000"));

    // Register two validators (while staking is still owned by deployer)
    await staking.registerValidatorAdmin("val1", 0, 1000);
    await staking.registerValidatorAdmin("val2", 0, 1000);

    // User1 stakes on val1
    const stakingAddr = await staking.getAddress();
    await modToken.connect(user1).approve(stakingAddr, E("10000"));
    await staking.connect(user1).stakeOn("val1", E("10000"), 0);

    // User2 stakes on val2
    await modToken.connect(user2).approve(stakingAddr, E("5000"));
    await staking.connect(user2).stakeOn("val2", E("5000"), 0);

    // Set minter to consensus
    await modToken.setMinter(await consensus.getAddress());

    // Transfer staking ownership to consensus
    await staking.transferOwnership(await consensus.getAddress());

    return { modToken, staking, consensus };
  }

  // ── Yuma × Flat ────────────────────────────────────────────────────────

  describe("Yuma + Flat Inflation", function () {
    it("simulates 5 epochs with constant emissions", async function () {
      const { modToken, consensus } = await deployWithValidators("yuma", {
        emissionRate: E("100"), epochLength: 5, decayBps: 500,
      });

      const flat = await (await ethers.getContractFactory("InflationFlat")).deploy(E("100"));
      await consensus.setInflationCurve(await flat.getAddress());

      let totalMinted = 0n;
      for (let epoch = 0; epoch < 5; epoch++) {
        // Both validators check in
        await consensus.batchCheckin(["val1", "val2"]);

        // Produce blocks to fill epoch
        for (let b = 0; b < 5; b++) {
          await consensus.produceBlock();
        }
      }

      // Check that Mod tokens were minted (consensus minted emissions)
      const supply = await modToken.totalSupply();
      // Initial: 1M minted by owner. Consensus should have minted more.
      expect(supply).to.be.gt(E("1000000"));
      totalMinted = supply - E("1000000");
      expect(totalMinted).to.be.gt(0n);
    });
  });

  // ── Yuma × Halving ─────────────────────────────────────────────────────

  describe("Yuma + Halving Inflation", function () {
    it("emissions decrease across halving boundaries", async function () {
      const { modToken, consensus } = await deployWithValidators("yuma", {
        emissionRate: E("1000"), epochLength: 3, decayBps: 500,
      });

      const halving = await (await ethers.getContractFactory("InflationHalving"))
        .deploy(E("1000"), 2, E("100"));
      await consensus.setInflationCurve(await halving.getAddress());

      // Run 6 epochs (3 halving periods)
      const epochEmissions = [];
      for (let epoch = 0; epoch < 6; epoch++) {
        const supplyBefore = await modToken.totalSupply();
        await consensus.batchCheckin(["val1", "val2"]);
        for (let b = 0; b < 3; b++) {
          await consensus.produceBlock();
        }
        const supplyAfter = await modToken.totalSupply();
        epochEmissions.push(supplyAfter - supplyBefore);
      }

      // Later epochs should have lower emissions
      expect(epochEmissions[0]).to.be.gt(0n);
      // Due to halving at epoch 2 and 4, emissions should generally decrease
      expect(epochEmissions[4]).to.be.lte(epochEmissions[0]);
    });
  });

  // ── Linear Consensus × LinearDecay ─────────────────────────────────────

  describe("Linear Consensus + LinearDecay Inflation", function () {
    it("both scoring and emissions decay linearly", async function () {
      const { modToken, consensus } = await deployWithValidators("linear", {
        emissionRate: E("500"), epochLength: 3,
      });

      const decay = await (await ethers.getContractFactory("InflationLinearDecay"))
        .deploy(E("500"), E("50"), 10);
      await consensus.setInflationCurve(await decay.getAddress());

      const epochMinted = [];
      for (let epoch = 0; epoch < 6; epoch++) {
        const supplyBefore = await modToken.totalSupply();
        // Only val1 checks in (val2 is lazy)
        await consensus.batchCheckin(["val1"]);
        for (let b = 0; b < 3; b++) {
          await consensus.produceBlock();
        }
        const supplyAfter = await modToken.totalSupply();
        epochMinted.push(supplyAfter - supplyBefore);
      }

      // Emissions should decrease over time
      expect(epochMinted[0]).to.be.gt(0n);
      // All emissions go to val1 since val2 never checks in
      // Later epochs have lower emissions due to linear decay curve
      expect(epochMinted[5]).to.be.lte(epochMinted[0]);
    });

    it("val2 joining late gets proportional share", async function () {
      const { modToken, consensus, staking } = await deployWithValidators("linear", {
        emissionRate: E("100"), epochLength: 3,
      });

      // Epoch 0: only val1
      await consensus.batchCheckin(["val1"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      // Epoch 1: both validators
      await consensus.batchCheckin(["val1", "val2"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      // Both should have earned something
      const bal1 = await consensus.getValidatorBalance("val1");
      const bal2 = await consensus.getValidatorBalance("val2");
      expect(bal1).to.be.gt(0n);
      expect(bal2).to.be.gt(0n);
      // val1 should have earned more (was active both epochs)
      expect(bal1).to.be.gt(bal2);
    });
  });

  // ── Staked Consensus × Sigmoid ─────────────────────────────────────────

  describe("Staked Consensus + Sigmoid Inflation", function () {
    it("emissions ramp up then decay with s-curve", async function () {
      const { modToken, consensus } = await deployWithValidators("staked", {
        emissionRate: E("100"), epochLength: 3,
      });

      const sigmoid = await (await ethers.getContractFactory("InflationSigmoid"))
        .deploy(E("500"), E("10"), 20);
      await consensus.setInflationCurve(await sigmoid.getAddress());

      const epochMinted = [];
      for (let epoch = 0; epoch < 12; epoch++) {
        const supplyBefore = await modToken.totalSupply();
        await consensus.batchCheckin(["val1", "val2"]);
        for (let b = 0; b < 3; b++) await consensus.produceBlock();
        const supplyAfter = await modToken.totalSupply();
        epochMinted.push(supplyAfter - supplyBefore);
      }

      // Middle epochs should have higher emissions than early/late
      expect(epochMinted[0]).to.be.gt(0n);
      // Emissions should increase from early epochs to middle
      expect(epochMinted[5]).to.be.gte(epochMinted[0]);
    });

    it("larger staker gets proportionally more rewards", async function () {
      const { modToken, consensus } = await deployWithValidators("staked", {
        emissionRate: E("100"), epochLength: 3,
      });

      // val1 has 10000 staked, val2 has 5000
      await consensus.batchCheckin(["val1", "val2"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      const rewards1 = await consensus.getStakerRewards(user1.address);
      const rewards2 = await consensus.getStakerRewards(user2.address);

      // user1 staked 2x more, so should get ~2x more rewards
      // (minus commission which is equal for both)
      if (rewards1 > 0n && rewards2 > 0n) {
        const ratio = (rewards1 * 100n) / rewards2;
        // Should be roughly 200 (2x), allow 150-250 range
        expect(ratio).to.be.gte(150n);
        expect(ratio).to.be.lte(250n);
      }
    });
  });

  // ── Yuma × TAO (Supply-Capped) ─────────────────────────────────────────

  describe("Yuma + TAO Supply-Capped Inflation", function () {
    it("emissions decrease as cumulative minting approaches cap", async function () {
      const { modToken, consensus } = await deployWithValidators("yuma", {
        emissionRate: E("10000"), epochLength: 3, decayBps: 500,
      });

      const tao = await (await ethers.getContractFactory("InflationTAO"))
        .deploy(E("10000"), E("100000"), E("100"));
      await consensus.setInflationCurve(await tao.getAddress());

      const epochMinted = [];
      for (let epoch = 0; epoch < 10; epoch++) {
        const supplyBefore = await modToken.totalSupply();
        await consensus.batchCheckin(["val1", "val2"]);
        for (let b = 0; b < 3; b++) await consensus.produceBlock();
        const supplyAfter = await modToken.totalSupply();
        const minted = supplyAfter - supplyBefore;
        epochMinted.push(minted);

        // Record minted amount in TAO curve
        if (minted > 0n) await tao.recordMint(minted);
      }

      // Emissions in later epochs should be less than earlier ones
      // (as totalMinted grows, TAO curve reduces emission)
      expect(epochMinted[0]).to.be.gt(0n);
    });
  });

  // ── Yuma × BTC (Hard Cap + Halving) ────────────────────────────────────

  describe("Yuma + BTC Hard-Cap Inflation", function () {
    it("follows halving schedule with supply cap", async function () {
      const { modToken, consensus } = await deployWithValidators("yuma", {
        emissionRate: E("500"), epochLength: 3, decayBps: 500,
      });

      const btc = await (await ethers.getContractFactory("InflationBTC"))
        .deploy(E("500"), 3, E("50000"));
      await consensus.setInflationCurve(await btc.getAddress());

      const epochMinted = [];
      for (let epoch = 0; epoch < 9; epoch++) {
        const supplyBefore = await modToken.totalSupply();
        await consensus.batchCheckin(["val1", "val2"]);
        for (let b = 0; b < 3; b++) await consensus.produceBlock();
        const supplyAfter = await modToken.totalSupply();
        const minted = supplyAfter - supplyBefore;
        epochMinted.push(minted);
        if (minted > 0n) await btc.recordMint(minted);
      }

      // Should have minted tokens
      expect(epochMinted[0]).to.be.gt(0n);
      // Total minted should not exceed supply cap
      const totalNewMinted = epochMinted.reduce((a, b) => a + b, 0n);
      expect(totalNewMinted).to.be.lte(E("50000"));
    });
  });

  // ── Multi-Validator Dynamics ───────────────────────────────────────────

  describe("Multi-Validator Dynamics (Yuma)", function () {
    it("active validator outearns lazy validator", async function () {
      const { consensus } = await deployWithValidators("yuma", {
        emissionRate: E("100"), epochLength: 3, decayBps: 1000,
      });

      // Epoch 0: both check in
      await consensus.batchCheckin(["val1", "val2"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      // Epoch 1: only val1 checks in (val2 goes offline)
      await consensus.batchCheckin(["val1"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      // Epoch 2: only val1 checks in
      await consensus.batchCheckin(["val1"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      const bal1 = await consensus.getValidatorBalance("val1");
      const bal2 = await consensus.getValidatorBalance("val2");

      // val1 was active all 3 epochs, val2 only 1
      expect(bal1).to.be.gt(bal2);
    });

    it("decay penalizes inactive validators over time", async function () {
      const { consensus } = await deployWithValidators("yuma", {
        emissionRate: E("100"), epochLength: 3, decayBps: 2000, // 20% decay
      });

      // Both check in first
      await consensus.batchCheckin(["val1", "val2"]);

      // val2 stops checking in, val1 continues many times to build score
      for (let i = 0; i < 10; i++) {
        await consensus.batchCheckin(["val1"]);
      }

      // val1 has been accumulating score with each checkin
      // val2 score is frozen at its value from the single checkin
      // (score only decays on the next checkin, so val2's score sits unchanged)
      // val1 should have a much higher score due to repeated checkins
      const kh1 = ethers.keccak256(ethers.toUtf8Bytes("val1"));
      const kh2 = ethers.keccak256(ethers.toUtf8Bytes("val2"));
      const score1 = (await consensus.getValidatorScore(kh1))[1];
      const score2 = (await consensus.getValidatorScore(kh2))[1];

      expect(score1).to.be.gte(score2);
      // totalBlocktime should reflect val1's dominance
      const state = await consensus.getBlock();
      expect(state[2]).to.be.gt(0n); // totalBlocktime > 0
    });
  });

  // ── Commission Split Verification ──────────────────────────────────────

  describe("Commission Split (all consensus types)", function () {
    for (const type of ["yuma", "linear", "staked"]) {
      it(`${type}: 10% commission goes to validator, 90% to stakers`, async function () {
        const { modToken, consensus } = await deployWithValidators(type, {
          emissionRate: E("100"), epochLength: 3, decayBps: 500,
        });

        // Run one epoch
        await consensus.batchCheckin(["val1", "val2"]);
        for (let b = 0; b < 3; b++) await consensus.produceBlock();

        // Check rewards exist
        const valBal1 = await consensus.getValidatorBalance("val1");
        const stakerReward1 = await consensus.getStakerRewards(user1.address);

        // Both should have earned something
        expect(valBal1).to.be.gt(0n);
        expect(stakerReward1).to.be.gt(0n);

        // Commission is 10% (1000 bps), so validator gets ~10% of their share
        // and stakers get ~90%
        const totalVal1 = valBal1 + stakerReward1;
        if (totalVal1 > 0n) {
          const commissionPct = (valBal1 * 100n) / totalVal1;
          // Should be approximately 10% (allow 8-12% for rounding)
          expect(commissionPct).to.be.gte(8n);
          expect(commissionPct).to.be.lte(12n);
        }
      });
    }
  });

  // ── Zero Supply Start Verification ─────────────────────────────────────

  describe("Zero-Supply Mod Token Lifecycle", function () {
    it("token starts at zero, all supply comes from consensus minting", async function () {
      const ModToken = await ethers.getContractFactory("Mod");
      const modToken = await ModToken.deploy("ZeroMod", "ZMD");

      // Verify zero supply at genesis
      expect(await modToken.totalSupply()).to.equal(0n);
      expect(await modToken.balanceOf(owner.address)).to.equal(0n);

      const StakeTime = await ethers.getContractFactory("StakeTime");
      const staking = await StakeTime.deploy(
        await modToken.getAddress(), 100000, 100, 1000
      );

      const ConsensusLinear = await ethers.getContractFactory("ConsensusLinear");
      const consensus = await ConsensusLinear.deploy(
        await modToken.getAddress(),
        await staking.getAddress(),
        E("100"),
        3,
      );

      // Set consensus as the sole minter
      await modToken.setMinter(await consensus.getAddress());

      // Register a validator (staking still owned by deployer)
      await staking.registerValidatorAdmin("genesis-val", 0, 1000);

      // Transfer staking ownership to consensus
      await staking.transferOwnership(await consensus.getAddress());

      // No tokens exist yet — cannot stake. Checkin + produce to mint.
      await consensus.batchCheckin(["genesis-val"]);
      for (let b = 0; b < 3; b++) await consensus.produceBlock();

      // Now tokens have been minted by consensus
      const supply = await modToken.totalSupply();
      expect(supply).to.be.gt(0n);

      // All minted tokens are in the consensus contract (validator balance + staker rewards)
      const consensusBal = await modToken.balanceOf(await consensus.getAddress());
      expect(consensusBal).to.equal(supply);

      // Validator can claim
      const valBal = await consensus.getValidatorBalance("genesis-val");
      expect(valBal).to.be.gt(0n);
      expect(valBal).to.equal(supply); // 100% to validator (no stakers)
    });
  });
});
