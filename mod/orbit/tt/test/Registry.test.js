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
    const Subnet = await ethers.getContractFactory("Subnet");
    const sub = await Subnet.deploy(name, symbol, ethers.parseEther("1000000"));
    await sub.waitForDeployment();

    const Staking = await ethers.getContractFactory("Staking");
    const st = await Staking.deploy(
      await sub.getAddress(), MAX_LOCK_BLOCKS, MAX_STAKERS, DEFAULT_COMMISSION_BPS
    );
    await st.waitForDeployment();

    // For Registry tests, staking address doubles as consensus address
    return { subnet: sub, staking: st, consensus: st };
  }

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Subnet = await ethers.getContractFactory("Subnet");
    govToken = await Subnet.deploy("Governance", "GOV", ethers.parseEther("10000000"));
    await govToken.waitForDeployment();

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
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());
      expect(await registry.getSubnetCount()).to.equal(2);
      expect(await govToken.balanceOf(await registry.getAddress())).to.equal(REGISTRATION_COST * BigInt(2));
    });

    it("rejects zero subnet address", async function () {
      await expect(
        registry.registerSubnet("bad", ethers.ZeroAddress, await staking1.getAddress(), await consensus1.getAddress())
      ).to.be.revertedWith("zero subnet");
    });

    it("rejects zero stakeTime address", async function () {
      await expect(
        registry.registerSubnet("bad", await subnet1.getAddress(), ethers.ZeroAddress, await consensus1.getAddress())
      ).to.be.revertedWith("zero stakeTime");
    });

    it("reverts without governance token approval", async function () {
      const [, , , user3] = await ethers.getSigners();
      await expect(
        registry.connect(user3).registerSubnet("bad", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress())
      ).to.be.reverted;
    });

    it("assigns sequential IDs", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());

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

  describe("Views", function () {
    beforeEach(async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());
    });

    it("getSubnet returns correct data", async function () {
      const s = await registry.getSubnet(0);
      expect(s.name).to.equal("sub1");
      expect(s.subnet).to.equal(await subnet1.getAddress());
      expect(s.stakeTime).to.equal(await staking1.getAddress());
      expect(s.consensus).to.equal(await consensus1.getAddress());
      expect(s.active).to.be.true;
    });

    it("getAllSubnets returns all active subnets", async function () {
      const all = await registry.getAllSubnets();
      expect(all.length).to.equal(2);
    });

    it("getStakeScore includes locked stake plus STT supply", async function () {
      expect(await registry.getStakeScore(0)).to.equal(REGISTRATION_COST);

      await staking1.registerValidatorAdmin("v1", 1, 1000);
      await subnet1.approve(await staking1.getAddress(), ethers.MaxUint256);
      await staking1.stakeOn("v1", ethers.parseEther("1000"), 0);

      expect(await registry.getStakeScore(0)).to.equal(REGISTRATION_COST + ethers.parseEther("1000"));
    });
  });

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
      expect(await registry.getLockedStake(0)).to.equal(0);
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

  describe("Weakest Subnet Replacement", function () {
    it("getWeakestSubnet finds the subnet with lowest score", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());

      await staking1.registerValidatorAdmin("v1", 1, 1000);
      await subnet1.approve(await staking1.getAddress(), ethers.MaxUint256);
      await staking1.stakeOn("v1", ethers.parseEther("5000"), 0);

      await mine(IMMUNITY_PERIOD + 1);

      const [weakId, weakScore, found] = await registry.getWeakestSubnet();
      expect(found).to.be.true;
      expect(weakId).to.equal(1);
      expect(weakScore).to.equal(REGISTRATION_COST);
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
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());

      const [, , found] = await registry.getWeakestSubnet();
      expect(found).to.be.false;
    });
  });

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

  describe("getAllSubnets after deregistration", function () {
    it("excludes deregistered subnets", async function () {
      await registry.registerSubnet("sub1", await subnet1.getAddress(), await staking1.getAddress(), await consensus1.getAddress());
      await registry.registerSubnet("sub2", await subnet2.getAddress(), await staking2.getAddress(), await consensus2.getAddress());
      await registry.deregisterSubnet(0);

      const all = await registry.getAllSubnets();
      expect(all.length).to.equal(1);
      expect(all[0].name).to.equal("sub2");
    });
  });
});
