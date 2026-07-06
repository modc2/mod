const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Registry", function () {
  let registry, owner, user1, user2;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Registry = await ethers.getContractFactory("Registry");
    registry = await Registry.deploy();
    await registry.waitForDeployment();
  });

  describe("Mod Registration", function () {
    const modName = "TestMod";
    const modData = "ipfs://QmTest123";

    it("Should register mod correctly with name", async function () {
      await expect(registry.connect(user1).registerMod(modName, modData))
        .to.emit(registry, "ModRegistered");

      const [modOwner, name, data] = await registry.getMod(1);
      expect(modOwner).to.equal(user1.address);
      expect(name).to.equal(modName);
      expect(data).to.equal(modData);
    });

    it("Should enforce name uniqueness per creator", async function () {
      await registry.connect(user1).registerMod(modName, modData);

      await expect(
        registry.connect(user1).registerMod(modName, modData)
      ).to.be.revertedWith("Name already exists for this creator");
    });

    it("Should allow same name for different creators", async function () {
      await registry.connect(user1).registerMod(modName, modData);
      await expect(registry.connect(user2).registerMod(modName, modData))
        .to.emit(registry, "ModRegistered");

      const [owner1] = await registry.getMod(1);
      const [owner2] = await registry.getMod(2);
      expect(owner1).to.equal(user1.address);
      expect(owner2).to.equal(user2.address);
    });

    it("Should increment mod ID", async function () {
      await registry.connect(user1).registerMod(modName, modData);
      await registry.connect(user1).registerMod("TestMod2", modData);

      expect(await registry.nextModId()).to.equal(3);
    });

    it("Should track user mods", async function () {
      await registry.connect(user1).registerMod(modName, modData);
      await registry.connect(user1).registerMod("TestMod2", modData);

      const userMods = await registry.getUserMods(user1.address);
      expect(userMods.length).to.equal(2);
    });

    it("Should check if name is taken", async function () {
      expect(await registry.isNameTaken(user1.address, modName)).to.be.false;

      await registry.connect(user1).registerMod(modName, modData);

      expect(await registry.isNameTaken(user1.address, modName)).to.be.true;
      expect(await registry.isNameTaken(user2.address, modName)).to.be.false;
    });
  });

  describe("Mod Update", function () {
    const modName = "TestMod";
    const modData = "ipfs://QmTest123";
    const newData = "ipfs://QmTest456";

    beforeEach(async function () {
      await registry.connect(user1).registerMod(modName, modData);
    });

    it("Should update mod data", async function () {
      await expect(registry.connect(user1).updateMod(1, newData))
        .to.emit(registry, "ModUpdated");

      const [, , data] = await registry.getMod(1);
      expect(data).to.equal(newData);
    });

    it("Should reject update from non-owner", async function () {
      await expect(
        registry.connect(user2).updateMod(1, newData)
      ).to.be.revertedWith("Not mod owner");
    });
  });

  describe("Mod Removal", function () {
    const modName = "TestMod";
    const modData = "ipfs://QmTest123";

    beforeEach(async function () {
      await registry.connect(user1).registerMod(modName, modData);
    });

    it("Should remove mod correctly", async function () {
      await expect(registry.connect(user1).removeMod(1))
        .to.emit(registry, "ModRemoved");

      const [modOwner] = await registry.getMod(1);
      expect(modOwner).to.equal(ethers.ZeroAddress);
    });

    it("Should remove from user mods list", async function () {
      await registry.connect(user1).removeMod(1);

      const userMods = await registry.getUserMods(user1.address);
      expect(userMods.length).to.equal(0);
    });

    it("Should free up name for reuse after removal", async function () {
      await registry.connect(user1).removeMod(1);

      expect(await registry.isNameTaken(user1.address, modName)).to.be.false;

      await expect(registry.connect(user1).registerMod(modName, modData))
        .to.emit(registry, "ModRegistered");
    });
  });

  describe("Ownership Transfer", function () {
    const modName = "TestMod";
    const modData = "ipfs://QmTest123";

    beforeEach(async function () {
      await registry.connect(user1).registerMod(modName, modData);
    });

    it("Should transfer ownership correctly", async function () {
      await expect(registry.connect(user1).transferOwnership(1, user2.address))
        .to.emit(registry, "OwnershipTransferred");

      const [modOwner] = await registry.getMod(1);
      expect(modOwner).to.equal(user2.address);
    });

    it("Should update user mods lists", async function () {
      await registry.connect(user1).transferOwnership(1, user2.address);

      const user1Mods = await registry.getUserMods(user1.address);
      const user2Mods = await registry.getUserMods(user2.address);

      expect(user1Mods.length).to.equal(0);
      expect(user2Mods.length).to.equal(1);
    });

    it("Should update name ownership mappings", async function () {
      await registry.connect(user1).transferOwnership(1, user2.address);

      expect(await registry.isNameTaken(user1.address, modName)).to.be.false;
      expect(await registry.isNameTaken(user2.address, modName)).to.be.true;
    });

    it("Should reject transfer if name exists for new owner", async function () {
      await registry.connect(user2).registerMod(modName, modData);

      await expect(
        registry.connect(user1).transferOwnership(1, user2.address)
      ).to.be.revertedWith("Name already exists for new owner");
    });
  });
});
