const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Splitter", function () {
  let splitter, alice, bob, payer;
  const ONE = ethers.parseEther("1");

  beforeEach(async function () {
    [payer, alice, bob] = await ethers.getSigners();
    splitter = await (await ethers.getContractFactory("Splitter"))
      .deploy([alice.address, bob.address], [3, 1]);
    await splitter.waitForDeployment();
  });

  it("splits by weight", async function () {
    await payer.sendTransaction({ to: await splitter.getAddress(), value: ONE });
    expect(await splitter.totalShares()).to.equal(4n);
    expect(await splitter.pending(alice.address)).to.equal((ONE * 3n) / 4n);
    expect(await splitter.pending(bob.address)).to.equal(ONE / 4n);
    expect(await splitter.pending(payer.address)).to.equal(0n);
  });

  it("releases once, then owes nothing", async function () {
    await payer.sendTransaction({ to: await splitter.getAddress(), value: ONE });
    await expect(splitter.release(alice.address))
      .to.changeEtherBalance(alice, (ONE * 3n) / 4n);
    expect(await splitter.pending(alice.address)).to.equal(0n);
    await expect(splitter.release(alice.address)).to.be.revertedWith("nothing due");
  });

  it("keeps owing after later payments", async function () {
    await payer.sendTransaction({ to: await splitter.getAddress(), value: ONE });
    await splitter.release(bob.address);
    await payer.sendTransaction({ to: await splitter.getAddress(), value: ONE });
    expect(await splitter.pending(bob.address)).to.equal(ONE / 4n);
  });

  it("refuses a zero-share payee", async function () {
    const Splitter = await ethers.getContractFactory("Splitter");
    await expect(Splitter.deploy([alice.address], [0])).to.be.revertedWith("bad payee");
  });
});
