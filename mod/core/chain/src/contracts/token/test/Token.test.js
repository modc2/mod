const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Token", function () {
  let token, owner, user1;
  const INITIAL_SUPPLY = ethers.parseEther("1000000");

  beforeEach(async function () {
    [owner, user1] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("Token");
    token = await Token.deploy("Test Token", "TST", INITIAL_SUPPLY);
    await token.waitForDeployment();
  });

  describe("Deployment", function () {
    it("Should set correct name and symbol", async function () {
      expect(await token.name()).to.equal("Test Token");
      expect(await token.symbol()).to.equal("TST");
    });

    it("Should mint initial supply to deployer", async function () {
      expect(await token.balanceOf(owner.address)).to.equal(INITIAL_SUPPLY);
    });
  });

  describe("Transfers", function () {
    it("Should transfer tokens", async function () {
      const amount = ethers.parseEther("100");
      await token.transfer(user1.address, amount);
      expect(await token.balanceOf(user1.address)).to.equal(amount);
    });

    it("Should approve and transferFrom", async function () {
      const amount = ethers.parseEther("100");
      await token.approve(user1.address, amount);
      await token.connect(user1).transferFrom(owner.address, user1.address, amount);
      expect(await token.balanceOf(user1.address)).to.equal(amount);
    });
  });
});
