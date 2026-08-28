const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("MyToken", function () {
  let token, owner, user;
  const SUPPLY = 1_000_000n;
  const TOTAL = SUPPLY * 10n ** 18n;

  beforeEach(async function () {
    [owner, user] = await ethers.getSigners();
    token = await (await ethers.getContractFactory("MyToken"))
      .deploy("My Token", "MTK", SUPPLY);
    await token.waitForDeployment();
  });

  it("mints the whole supply to the deployer", async function () {
    expect(await token.name()).to.equal("My Token");
    expect(await token.symbol()).to.equal("MTK");
    expect(await token.totalSupply()).to.equal(TOTAL);
    expect(await token.balanceOf(owner.address)).to.equal(TOTAL);
  });

  it("transfers", async function () {
    const amount = ethers.parseEther("100");
    await expect(token.transfer(user.address, amount))
      .to.changeTokenBalances(token, [owner, user], [-amount, amount]);
  });

  it("lets only the owner mint more", async function () {
    await token.mint(user.address, 1000n);
    expect(await token.balanceOf(user.address)).to.equal(1000n);
    await expect(token.connect(user).mint(user.address, 1000n))
      .to.be.revertedWith("Ownable: caller is not the owner");
  });
});
