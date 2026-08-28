const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Vault", function () {
  let vault, user;
  const ONE = ethers.parseEther("1");

  beforeEach(async function () {
    [, user] = await ethers.getSigners();
    vault = await (await ethers.getContractFactory("Vault")).deploy();
    await vault.waitForDeployment();
  });

  it("credits a deposit", async function () {
    await expect(vault.connect(user).deposit({ value: ONE }))
      .to.emit(vault, "Deposit")
      .withArgs(user.address, ONE);
    expect(await vault.balanceOf(user.address)).to.equal(ONE);
    expect(await vault.totalDeposits()).to.equal(ONE);
  });

  it("credits a bare transfer through receive()", async function () {
    await user.sendTransaction({ to: await vault.getAddress(), value: ONE });
    expect(await vault.balanceOf(user.address)).to.equal(ONE);
  });

  it("rejects an empty deposit", async function () {
    await expect(vault.connect(user).deposit({ value: 0 })).to.be.revertedWith("zero deposit");
  });

  it("pays out a withdrawal and no more", async function () {
    await vault.connect(user).deposit({ value: ONE });
    await expect(vault.connect(user).withdraw(ONE)).to.changeEtherBalance(user, ONE);
    expect(await vault.balanceOf(user.address)).to.equal(0n);
    await expect(vault.connect(user).withdraw(1)).to.be.revertedWith("insufficient");
  });
});
