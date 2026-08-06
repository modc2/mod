const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Counter", function () {
  let counter, owner, stranger;

  beforeEach(async function () {
    [owner, stranger] = await ethers.getSigners();
    counter = await (await ethers.getContractFactory("Counter")).deploy(5);
    await counter.waitForDeployment();
  });

  it("starts where the constructor said", async function () {
    expect(await counter.count()).to.equal(5n);
    expect(await counter.owner()).to.equal(owner.address);
  });

  it("bumps and emits", async function () {
    await expect(counter.bump())
      .to.emit(counter, "Bumped")
      .withArgs(owner.address, 6n);
    expect(await counter.count()).to.equal(6n);
  });

  it("only the owner can reset", async function () {
    await expect(counter.connect(stranger).reset()).to.be.revertedWith("not owner");
    await counter.reset();
    expect(await counter.count()).to.equal(0n);
  });
});
