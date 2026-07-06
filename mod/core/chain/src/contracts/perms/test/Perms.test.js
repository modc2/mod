const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Perms", function () {
  let perms, owner, user1, user2;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Perms = await ethers.getContractFactory("Perms");
    perms = await Perms.deploy();
    await perms.waitForDeployment();
  });

  describe("Key Management", function () {
    const parentKey = ethers.toUtf8Bytes("parent");
    const childKey = ethers.toUtf8Bytes("child");

    it("Should add key correctly", async function () {
      await expect(perms.connect(user1).addKey(parentKey, childKey))
        .to.emit(perms, "KeyAdded");

      const keys = await perms.getKeys(parentKey);
      expect(keys.length).to.equal(1);
    });

    it("Should set owner on first add", async function () {
      await perms.connect(user1).addKey(parentKey, childKey);

      expect(await perms.keyOwners(parentKey)).to.equal(user1.address);
    });

    it("Should reject add from non-owner", async function () {
      await perms.connect(user1).addKey(parentKey, childKey);

      await expect(
        perms.connect(user2).addKey(parentKey, ethers.toUtf8Bytes("child2"))
      ).to.be.revertedWith("Only parent key owner can add");
    });
  });

  describe("Key Removal", function () {
    const parentKey = ethers.toUtf8Bytes("parent");
    const childKey = ethers.toUtf8Bytes("child");

    beforeEach(async function () {
      await perms.connect(user1).addKey(parentKey, childKey);
    });

    it("Should remove key correctly", async function () {
      await expect(perms.connect(user1).removeKey(parentKey, childKey))
        .to.emit(perms, "KeyRemoved");

      const keys = await perms.getKeys(parentKey);
      expect(keys.length).to.equal(0);
    });

    it("Should remove key by index", async function () {
      await perms.connect(user1).removeKeyAtIndex(parentKey, 0);

      const keys = await perms.getKeys(parentKey);
      expect(keys.length).to.equal(0);
    });
  });

  describe("Batch Operations", function () {
    const parentKey = ethers.toUtf8Bytes("parent");
    const childKeys = [
      ethers.toUtf8Bytes("child1"),
      ethers.toUtf8Bytes("child2"),
      ethers.toUtf8Bytes("child3")
    ];

    it("Should set keys in batch", async function () {
      await expect(perms.connect(user1).setKeys(parentKey, childKeys))
        .to.emit(perms, "KeysSet");

      const keys = await perms.getKeys(parentKey);
      expect(keys.length).to.equal(3);
    });

    it("Should replace existing keys", async function () {
      await perms.connect(user1).setKeys(parentKey, childKeys);

      const newKeys = [ethers.toUtf8Bytes("new1")];
      await perms.connect(user1).setKeys(parentKey, newKeys);

      const keys = await perms.getKeys(parentKey);
      expect(keys.length).to.equal(1);
    });
  });

  describe("Ownership Transfer", function () {
    const parentKey = ethers.toUtf8Bytes("parent");
    const childKey = ethers.toUtf8Bytes("child");

    beforeEach(async function () {
      await perms.connect(user1).addKey(parentKey, childKey);
    });

    it("Should transfer key ownership", async function () {
      await expect(perms.connect(user1).transferKeyOwnership(parentKey, user2.address))
        .to.emit(perms, "OwnershipTransferred");

      expect(await perms.keyOwners(parentKey)).to.equal(user2.address);
    });
  });

  describe("Limits", function () {
    it("Should enforce max child keys", async function () {
      const parentKey = ethers.toUtf8Bytes("parent");
      const maxKeys = await perms.maxChildKeys();

      const keys = [];
      for (let i = 0; i < Number(maxKeys) + 1; i++) {
        keys.push(ethers.toUtf8Bytes(`child${i}`));
      }

      await expect(
        perms.connect(user1).setKeys(parentKey, keys)
      ).to.be.revertedWith("Exceeds max child keys");
    });
  });
});
