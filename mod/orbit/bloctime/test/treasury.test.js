const { expect } = require("chai");
const { ethers } = require("hardhat");

// The treasury mints the NativeToken 1:1 per dollar of reserve deposited,
// and every fresh BlocTime adopts the 8-year max-lock default the owner
// can later change with setParams.

const SECONDS_PER_YEAR = 365 * 24 * 3600;
const MAX_LOCK_SECONDS = SECONDS_PER_YEAR * 8; // 252,288,000

describe("Treasury", function () {
  let owner, alice, usd, token, treasury, bloctime;

  beforeEach(async function () {
    [owner, alice] = await ethers.getSigners();

    const MockUSD = await ethers.getContractFactory("MockUSD");
    usd = await MockUSD.deploy();

    const NativeToken = await ethers.getContractFactory("NativeToken");
    token = await NativeToken.deploy(ethers.parseEther("1000000"));

    const Treasury = await ethers.getContractFactory("Treasury");
    treasury = await Treasury.deploy(await token.getAddress(), await usd.getAddress());

    // The deploy recipe: the treasury owns the token, so it alone can mint.
    await token.transferOwnership(await treasury.getAddress());

    const BlocTime = await ethers.getContractFactory("BlocTime");
    bloctime = await BlocTime.deploy(await token.getAddress(), MAX_LOCK_SECONDS, 1_000_000);
  });

  it("mints 1 token per dollar across the decimal gap (6 -> 18)", async function () {
    await usd.faucet(alice.address, 250_000_000); // $250 in 6-decimal units
    await usd.connect(alice).approve(await treasury.getAddress(), 250_000_000);
    await treasury.connect(alice).deposit(250_000_000);
    expect(await token.balanceOf(alice.address)).to.equal(ethers.parseEther("250"));
    expect(await treasury.reserveBalance()).to.equal(250_000_000);
    expect(await treasury.totalDeposited()).to.equal(250_000_000);
  });

  it("redeems 1:1, burning only what the payout covers", async function () {
    await usd.faucet(alice.address, 100_000_000); // $100
    await usd.connect(alice).approve(await treasury.getAddress(), 100_000_000);
    await treasury.connect(alice).deposit(100_000_000);

    // Ask to redeem $40 plus sub-cent dust: dust must survive in the wallet.
    const dust = 123n;
    const ask = ethers.parseEther("40") + dust;
    await token.connect(alice).approve(await treasury.getAddress(), ask);
    await treasury.connect(alice).redeem(ask);

    expect(await usd.balanceOf(alice.address)).to.equal(40_000_000);
    // Only the covered 40e18 burned — the dusty remainder of the ask stays put.
    expect(await token.balanceOf(alice.address)).to.equal(ethers.parseEther("60"));
    expect(await treasury.totalRedeemed()).to.equal(40_000_000);
  });

  it("rejects deposits without approval and dust-only redemptions", async function () {
    await usd.faucet(alice.address, 5_000_000);
    await expect(treasury.connect(alice).deposit(5_000_000)).to.be.reverted;
    await expect(treasury.connect(alice).redeem(999)).to.be.revertedWith("Amount too small");
  });

  it("only the treasury can mint once it owns the token", async function () {
    await expect(token.connect(alice).mint(alice.address, 1)).to.be.reverted;
    await expect(token.connect(owner).mint(owner.address, 1)).to.be.reverted;
    expect(await token.owner()).to.equal(await treasury.getAddress());
  });

  it("owner can spend the treasury; redemptions ride on what remains", async function () {
    await usd.faucet(alice.address, 10_000_000);
    await usd.connect(alice).approve(await treasury.getAddress(), 10_000_000);
    await treasury.connect(alice).deposit(10_000_000);

    await expect(treasury.connect(alice).ownerWithdraw(1)).to.be.reverted;
    await treasury.connect(owner).ownerWithdraw(10_000_000);
    expect(await usd.balanceOf(owner.address)).to.equal(10_000_000);

    await token.connect(alice).approve(await treasury.getAddress(), ethers.parseEther("10"));
    await expect(treasury.connect(alice).redeem(ethers.parseEther("10"))).to.be.reverted;
  });

  it("minted dollars stake like any NativeToken", async function () {
    await usd.faucet(alice.address, 10_000_000); // $10
    await usd.connect(alice).approve(await treasury.getAddress(), 10_000_000);
    await treasury.connect(alice).deposit(10_000_000);

    await token.connect(alice).approve(await bloctime.getAddress(), ethers.parseEther("10"));
    await bloctime.connect(alice).stake(ethers.parseEther("10"), 3600);
    // $10 x 3600s x flat 1x curve = 36,000 BLOC
    expect(await bloctime.balanceOf(alice.address)).to.equal(ethers.parseEther("36000"));
  });

  it("BlocTime adopts the 8-year max lock and the owner can change it", async function () {
    const params = await bloctime.params();
    expect(params.maxLockSeconds).to.equal(MAX_LOCK_SECONDS);

    await expect(bloctime.connect(alice).setParams(SECONDS_PER_YEAR, 2)).to.be.reverted;
    await bloctime.connect(owner).setParams(SECONDS_PER_YEAR * 4, 2);
    expect((await bloctime.params()).maxLockSeconds).to.equal(SECONDS_PER_YEAR * 4);

    await expect(
      bloctime.connect(alice).stake(1, SECONDS_PER_YEAR * 5)
    ).to.be.revertedWith("Exceeds max lock");
  });
});
