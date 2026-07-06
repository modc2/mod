const { expect } = require("chai");
const { ethers } = require("hardhat");
const { mine } = require("@nomicfoundation/hardhat-network-helpers");

describe("Registry", function () {
  let registry, govToken;
  let subnet1, staking1, consensus1;
  let subnet2, staking2, consensus2;
  let owner, user1, user2;

  const MAX_LOCK_BLOCKS = 100000;
  const MAX_STAKERS = 10;
  const DEFAULT_COMMISSION_BPS = 1000;
  const IMMUNITY_PERIOD = 20;
  const REGISTRATION_COST = ethers.parseEther("1000");

  async function deploySubnetStack(name, symbol) {
    const ModToken = await ethers.getContractFactory("Mod");
    const sub = await ModToken.deploy(name, symbol);
    await sub.waitForDeployment();

    // Mint tokens for testing
    await sub.setMinter(owner.address);
    await sub.mint(owner.address, ethers.parseEther("1000000"));

    const StakeTime = await ethers.getContractFactory("StakeTime");
    const st = await StakeTime.deploy(
      await sub.getAddress(), MAX_LOCK_BLOCKS, MAX_STAKERS, DEFAULT_COMMISSION_BPS
    );
    await st.waitForDeployment();

    return { subnet: sub, staking: st, consensus: st };
  }

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const ModToken = await ethers.getContractFactory("Mod");
    govToken = await ModToken.deploy("Governance", "GOV");
    await govToken.waitForDeployment();

    // Mint governance tokens for testing
    await govToken.setMinter(owner.address);
    await govToken.mint(owner.address, ethers.parseEther("10000000"));

    const s1 = await deploySubnetStack("Net1", "N1");
    subnet1 = s1.subnet; staking1 = s1.staking; consensus1 = s1.consensus;

    const s2 = await deploySubnetStack("Net2", "N2");
    subnet2 = s2.subnet; staking2 = s2.staking; consensus2 = s2.consensus;

    const Registry = await ethers.getContractFactory("Registry");
    registry = await Registry.deploy(IMMUNITY_PERIOD, await govToken.getAddress(), REGISTRATION_COST);
    await registry.waitForDeployment();

    await govToken.approve(await registry.getAddress(), ethers.MaxUint256);

    await govToken.transfer(user1.address, ethers.parseEther("100000"));
    await govToken.transfer(user2.address, ethers.parseEther("100000"));
    await govToken.connect(user1).approve(await registry.getAddress(), ethers.MaxUint256);
    await govToken.connect(user2).approve(await registry.getAddress(), ethers.MaxUint256);
  });

  // ── Helper: register both subnets and get STT by staking ────────────

  async function registerBothSubnets() {
    await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
    await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());
  }

  async function getSTT(staking, subnet, sttToken, user, amount) {
    // Transfer subnet tokens to user, approve staking, stake to get STT
    await subnet.transfer(user.address, amount);
    await subnet.connect(user).approve(await staking.getAddress(), amount);
    // Register a validator first if needed
    try {
      await staking.registerValidatorAdmin("boost-val", 1, 1000);
    } catch { /* already registered */ }
    await staking.connect(user).stakeOn("boost-val", amount, 0);
    // User now has STT (ERC20 from StakeTime contract)
  }

  // ── Registration ────────────────────────────────────────────────────

  describe("Registration", function () {
    it("registers a subnet and locks governance token", async function () {
      const balBefore = await govToken.balanceOf(owner.address);
      await expect(
        registry.registerSubnet("genesis", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress())
      ).to.emit(registry, "SubnetRegistered");

      expect(await registry.getSubnetCount()).to.equal(1);

      const balAfter = await govToken.balanceOf(owner.address);
      expect(balBefore - balAfter).to.equal(REGISTRATION_COST);
      expect(await govToken.balanceOf(await registry.getAddress())).to.equal(REGISTRATION_COST);
      expect(await registry.getLockedStake(0)).to.equal(REGISTRATION_COST);
    });

    it("registers multiple subnets", async function () {
      await registerBothSubnets();
      expect(await registry.getSubnetCount()).to.equal(2);
      expect(await govToken.balanceOf(await registry.getAddress())).to.equal(REGISTRATION_COST * BigInt(2));
    });

    it("rejects zero subnet address", async function () {
      await expect(
        registry.registerSubnet("bad", ethers.ZeroAddress, await staking1.getAddress(), await consensus1.getAddress())
      ).to.be.revertedWith("zero subnet");
    });

    it("rejects zero staking address", async function () {
      await expect(
        registry.registerSubnet("bad", await subnet1.getAddress(), ethers.ZeroAddress, await consensus1.getAddress())
      ).to.be.revertedWith("zero staking");
    });

    it("reverts without governance token approval", async function () {
      const [, , , user3] = await ethers.getSigners();
      await expect(
        registry.connect(user3).registerSubnet("bad", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress())
      ).to.be.reverted;
    });

    it("assigns sequential IDs", async function () {
      await registerBothSubnets();
      const s1 = await registry.getSubnet(0);
      const s2 = await registry.getSubnet(1);
      expect(s1.name).to.equal("sub1");
      expect(s2.name).to.equal("sub2");
    });

    it("tracks owner subnets", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.connect(user1).registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());

      const ownerSubs = await registry.getOwnerSubnets(owner.address);
      const user1Subs = await registry.getOwnerSubnets(user1.address);
      expect(ownerSubs.length).to.equal(1);
      expect(user1Subs.length).to.equal(1);
    });
  });

  // ── Views ──────────────────────────────────────────────────────────

  describe("Views", function () {
    beforeEach(async function () {
      await registerBothSubnets();
    });

    it("getSubnet returns correct data", async function () {
      const s = await registry.getSubnet(0);
      expect(s.name).to.equal("sub1");
      expect(s.subnet).to.equal(await subnet1.getAddress());
      expect(s.staking).to.equal(await staking1.getAddress());
      expect(s.active).to.be.true;
    });

    it("getAllSubnets returns all active subnets", async function () {
      const all = await registry.getAllSubnets();
      expect(all.length).to.equal(2);
    });

    it("getStakeScore starts at lockedStake (no bloctime yet)", async function () {
      expect(await registry.getStakeScore(0)).to.equal(REGISTRATION_COST);
    });
  });

  // ── Immunity ──────────────────────────────────────────────────────

  describe("Immunity", function () {
    it("new subnets are immune", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      expect(await registry.isImmune(0)).to.be.true;
    });

    it("subnets lose immunity after period", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await mine(IMMUNITY_PERIOD + 1);
      expect(await registry.isImmune(0)).to.be.false;
    });

    it("owner can update immunity period", async function () {
      await registry.setImmunityPeriod(100);
      expect(await registry.immunityPeriod()).to.equal(100);
    });

    it("non-owner cannot update immunity period", async function () {
      await expect(
        registry.connect(user1).setImmunityPeriod(100)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });
  });

  // ── Deregistration ────────────────────────────────────────────────

  describe("Deregistration", function () {
    it("owner can deregister and get locked tokens back", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      const balBefore = await govToken.balanceOf(owner.address);

      await expect(registry.deregisterSubnet(0))
        .to.emit(registry, "SubnetDeregistered");

      expect(await registry.getSubnetCount()).to.equal(0);
      const s = await registry.getSubnet(0);
      expect(s.active).to.be.false;

      const balAfter = await govToken.balanceOf(owner.address);
      expect(balAfter - balBefore).to.equal(REGISTRATION_COST);
    });

    it("subnet owner can deregister their own", async function () {
      await registry.connect(user1).registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      const balBefore = await govToken.balanceOf(user1.address);
      await registry.connect(user1).deregisterSubnet(0);
      expect(await registry.getSubnetCount()).to.equal(0);

      const balAfter = await govToken.balanceOf(user1.address);
      expect(balAfter - balBefore).to.equal(REGISTRATION_COST);
    });

    it("non-owner cannot deregister others", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await expect(
        registry.connect(user1).deregisterSubnet(0)
      ).to.be.revertedWith("not authorized");
    });
  });

  // ── Bonding Curve: Boost ──────────────────────────────────────────

  describe("Bonding Curve Boost", function () {
    beforeEach(async function () {
      await registerBothSubnets();
    });

    it("boostSubnet deposits STT and mints shares", async function () {
      const sttAddr = await staking1.getAddress();
      const amount = ethers.parseEther("100");
      await getSTT(staking1, subnet1, staking1, user1, amount);

      // Approve registry to spend STT
      await staking1.connect(user1).approve(await registry.getAddress(), amount);

      const sttBal = await staking1.balanceOf(user1.address);
      expect(sttBal).to.be.gte(amount);

      await expect(
        registry.connect(user1).boostSubnet(0, sttAddr, amount)
      ).to.emit(registry, "Boosted");

      const shares = await registry.getUserShares(0, user1.address);
      expect(shares).to.be.gt(0);
      expect(await registry.subnetBloctime(0)).to.equal(amount);
      expect(await registry.subnetTotalShares(0)).to.equal(shares);
    });

    it("share price increases with each purchase (bonding curve)", async function () {
      const sttAddr = await staking1.getAddress();
      const amount = ethers.parseEther("100");

      // User1 buys first
      await getSTT(staking1, subnet1, staking1, user1, amount);
      await staking1.connect(user1).approve(await registry.getAddress(), amount);
      await registry.connect(user1).boostSubnet(0, sttAddr, amount);
      const shares1 = await registry.getUserShares(0, user1.address);

      // User2 buys second (same amount, should get fewer shares)
      await getSTT(staking1, subnet1, staking1, user2, amount);
      await staking1.connect(user2).approve(await registry.getAddress(), amount);
      await registry.connect(user2).boostSubnet(0, sttAddr, amount);
      const shares2 = await registry.getUserShares(0, user2.address);

      // Early buyer gets more shares per STT
      expect(shares1).to.be.gt(shares2);
    });

    it("sellBoost returns STT", async function () {
      const sttAddr = await staking1.getAddress();
      const amount = ethers.parseEther("100");
      await getSTT(staking1, subnet1, staking1, user1, amount);
      await staking1.connect(user1).approve(await registry.getAddress(), amount);
      await registry.connect(user1).boostSubnet(0, sttAddr, amount);

      const shares = await registry.getUserShares(0, user1.address);
      const balBefore = await staking1.balanceOf(user1.address);

      await expect(
        registry.connect(user1).sellBoost(0, shares, sttAddr)
      ).to.emit(registry, "BoostSold");

      const balAfter = await staking1.balanceOf(user1.address);
      expect(balAfter).to.be.gt(balBefore);
      expect(await registry.getUserShares(0, user1.address)).to.equal(0);
      expect(await registry.subnetTotalShares(0)).to.equal(0);
    });

    it("rejects invalid STT token", async function () {
      const amount = ethers.parseEther("100");
      // govToken is NOT a valid STT token
      await govToken.approve(await registry.getAddress(), amount);
      await expect(
        registry.boostSubnet(0, await govToken.getAddress(), amount)
      ).to.be.revertedWith("invalid STT token");
    });

    it("rejects boost on inactive subnet", async function () {
      await registry.deregisterSubnet(0);
      const sttAddr = await staking1.getAddress();
      await expect(
        registry.boostSubnet(0, sttAddr, 1)
      ).to.be.revertedWith("subnet not active");
    });

    it("cannot sell more shares than owned", async function () {
      const sttAddr = await staking1.getAddress();
      await expect(
        registry.connect(user1).sellBoost(0, 1, sttAddr)
      ).to.be.revertedWith("insufficient shares");
    });

    it("cannot withdraw STT type with insufficient reserves", async function () {
      const stt1Addr = await staking1.getAddress();
      const stt2Addr = await staking2.getAddress();
      const amount = ethers.parseEther("100");

      // Deposit STT from staking1
      await getSTT(staking1, subnet1, staking1, user1, amount);
      await staking1.connect(user1).approve(await registry.getAddress(), amount);
      await registry.connect(user1).boostSubnet(0, stt1Addr, amount);

      const shares = await registry.getUserShares(0, user1.address);

      // Try to withdraw as staking2's STT — should fail
      await expect(
        registry.connect(user1).sellBoost(0, shares, stt2Addr)
      ).to.be.revertedWith("insufficient reserves for token");
    });

    it("getStakeScore reflects bloctime deposits", async function () {
      const scoreBefore = await registry.getStakeScore(0);
      expect(scoreBefore).to.equal(REGISTRATION_COST);

      const sttAddr = await staking1.getAddress();
      const amount = ethers.parseEther("500");
      await getSTT(staking1, subnet1, staking1, user1, amount);
      await staking1.connect(user1).approve(await registry.getAddress(), amount);
      await registry.connect(user1).boostSubnet(0, sttAddr, amount);

      const scoreAfter = await registry.getStakeScore(0);
      expect(scoreAfter).to.equal(REGISTRATION_COST + amount);
    });

    it("multi-STT: deposit STT from subnet A to boost subnet B", async function () {
      // User1 gets STT from staking1 (subnet A) and boosts subnet B (id=1)
      const stt1Addr = await staking1.getAddress();
      const amount = ethers.parseEther("200");
      await getSTT(staking1, subnet1, staking1, user1, amount);
      await staking1.connect(user1).approve(await registry.getAddress(), amount);

      await expect(
        registry.connect(user1).boostSubnet(1, stt1Addr, amount)
      ).to.emit(registry, "Boosted");

      expect(await registry.subnetBloctime(1)).to.equal(amount);
      expect(await registry.sttReserves(1, stt1Addr)).to.equal(amount);
    });
  });

  // ── Bonding Curve Views ───────────────────────────────────────────

  describe("Bonding Curve Views", function () {
    beforeEach(async function () {
      await registerBothSubnets();
    });

    it("getBoostPrice returns cost for shares", async function () {
      const price = await registry.getBoostPrice(0, ethers.parseEther("1"));
      expect(price).to.be.gt(0);
    });

    it("getSellReturn returns 0 for oversized sell", async function () {
      const ret = await registry.getSellReturn(0, ethers.parseEther("999"));
      expect(ret).to.equal(0);
    });

    it("getPoolInfo returns correct data", async function () {
      const [totalShares, totalBloctime, currentPrice, locked] = await registry.getPoolInfo(0);
      expect(totalShares).to.equal(0);
      expect(totalBloctime).to.equal(0);
      expect(currentPrice).to.equal(0);
      expect(locked).to.equal(REGISTRATION_COST);
    });

    it("curveSlope is configurable by owner", async function () {
      await registry.setCurveSlope(ethers.parseEther("1"));
      expect(await registry.curveSlope()).to.equal(ethers.parseEther("1"));
    });

    it("non-owner cannot set curve slope", async function () {
      await expect(
        registry.connect(user1).setCurveSlope(1)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });
  });

  // ── Weakest Subnet ────────────────────────────────────────────────

  describe("Weakest Subnet with Bloctime", function () {
    it("weakest is the one with least bloctime + locked", async function () {
      await registerBothSubnets();

      // Boost subnet 0 with STT
      const sttAddr = await staking1.getAddress();
      const amount = ethers.parseEther("500");
      await getSTT(staking1, subnet1, staking1, user1, amount);
      await staking1.connect(user1).approve(await registry.getAddress(), amount);
      await registry.connect(user1).boostSubnet(0, sttAddr, amount);

      await mine(IMMUNITY_PERIOD + 1);

      // Subnet 1 has no bloctime, only locked GOV — it's weakest
      const [weakId, , found] = await registry.getWeakestSubnet();
      expect(found).to.be.true;
      expect(weakId).to.equal(1);

      const score0 = await registry.getStakeScore(0);
      const score1 = await registry.getStakeScore(1);
      expect(score0).to.be.gt(score1);
    });

    it("skips immune subnets when finding weakest", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await mine(IMMUNITY_PERIOD + 1);
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());

      const [weakId, , found] = await registry.getWeakestSubnet();
      expect(found).to.be.true;
      expect(weakId).to.equal(0);
    });

    it("returns not found when all subnets are immune", async function () {
      await registerBothSubnets();
      const [, , found] = await registry.getWeakestSubnet();
      expect(found).to.be.false;
    });
  });

  // ── Registration cost admin ───────────────────────────────────────

  describe("Registration cost admin", function () {
    it("owner can update registration cost", async function () {
      const newCost = ethers.parseEther("2000");
      await registry.setRegistrationCost(newCost);
      expect(await registry.getRegistrationCost()).to.equal(newCost);
    });

    it("non-owner cannot update registration cost", async function () {
      await expect(
        registry.connect(user1).setRegistrationCost(0)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });
  });

  // ── getAllSubnets after deregistration ─────────────────────────────

  describe("getAllSubnets after deregistration", function () {
    it("excludes deregistered subnets", async function () {
      await registerBothSubnets();
      await registry.deregisterSubnet(0);

      const all = await registry.getAllSubnets();
      expect(all.length).to.equal(1);
      expect(all[0].name).to.equal("sub2");
    });
  });
});
