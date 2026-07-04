const { expect } = require("chai");
const { ethers } = require("hardhat");

// USDC-style: 6 decimals, $1 priced at 8 decimals.
const USDC = (n) => ethers.parseUnits(n.toString(), 6);
const PRICE_1USD = 100000000n; // $1, 8 decimals
const OWNER_PCT = 2000; // 20%

describe("DeFi YieldVault", function () {
  let usdc, oracle, tokenGate, treasury, market, vault, adapter;
  let owner, alice, bob;

  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();

    const MockERC20 = await ethers.getContractFactory("MockERC20");
    usdc = await MockERC20.deploy("USD Coin", "USDC", 6);
    await usdc.waitForDeployment();

    const Oracle = await ethers.getContractFactory("ManualPriceOracle");
    oracle = await Oracle.deploy();
    await oracle.waitForDeployment();
    await oracle.setPrice(await usdc.getAddress(), PRICE_1USD, 8);

    const TokenGate = await ethers.getContractFactory("TokenGate");
    tokenGate = await TokenGate.deploy(await oracle.getAddress());
    await tokenGate.waitForDeployment();
    await tokenGate.whitelistToken(await usdc.getAddress());

    const Treasury = await ethers.getContractFactory("Treasury");
    treasury = await Treasury.deploy(OWNER_PCT, await tokenGate.getAddress());
    await treasury.waitForDeployment();

    const Market = await ethers.getContractFactory("Market");
    market = await Market.deploy("Native", "NATIVE", await treasury.getAddress(), await tokenGate.getAddress());
    await market.waitForDeployment();

    // Market IS the native ERC20 → nativeToken == market.
    const YieldVault = await ethers.getContractFactory("YieldVault");
    vault = await YieldVault.deploy(await market.getAddress(), await market.getAddress());
    await vault.waitForDeployment();

    const MockYieldAdapter = await ethers.getContractFactory("MockYieldAdapter");
    adapter = await MockYieldAdapter.deploy(await usdc.getAddress(), await vault.getAddress(), "Conservative");
    await adapter.waitForDeployment();

    await vault.addStrategy(await usdc.getAddress(), await adapter.getAddress(), "USDC · Conservative");

    // Fund actors.
    for (const u of [alice, bob, owner]) {
      await usdc.mint(u.address, USDC(1000000));
      await usdc.connect(u).approve(await vault.getAddress(), ethers.MaxUint256);
    }
    await usdc.connect(owner).approve(await adapter.getAddress(), ethers.MaxUint256);
  });

  // Owner simulates yield by funding the mock adapter's reserve.
  async function simulateYield(amount) {
    await adapter.connect(owner).addYield(amount);
  }

  it("deposits and withdraws principal 1:1", async function () {
    await vault.connect(alice).deposit(0, USDC(1000));
    expect(await vault.userShares(0, alice.address)).to.equal(USDC(1000));
    expect(await adapter.totalAssets()).to.equal(USDC(1000));

    const before = await usdc.balanceOf(alice.address);
    await vault.connect(alice).withdraw(0, USDC(1000));
    expect(await usdc.balanceOf(alice.address)).to.equal(before + USDC(1000));
    expect(await vault.userShares(0, alice.address)).to.equal(0n);
  });

  it("harvest routes profit through Market and distributes native pro-rata", async function () {
    await vault.connect(alice).deposit(0, USDC(1000));
    await simulateYield(USDC(100)); // +100 USDC yield
    expect(await vault.pendingProfit(0)).to.equal(USDC(100));

    await vault.harvest(0);

    // 100 USDC profit went to Treasury.
    expect(await usdc.balanceOf(await treasury.getAddress())).to.equal(USDC(100));

    // Native minted to vault = $100 in 8-dec, minus Market's 1% credit fee.
    const expected = (100n * 10n ** 8n * 9900n) / 10000n; // 99e8
    expect(await vault.pendingReward(0, alice.address)).to.be.closeTo(expected, 10n);

    await vault.connect(alice).claim(0);
    expect(await market.balanceOf(alice.address)).to.be.closeTo(expected, 10n);

    // Principal still fully redeemable.
    const before = await usdc.balanceOf(alice.address);
    await vault.connect(alice).withdraw(0, USDC(1000));
    expect(await usdc.balanceOf(alice.address)).to.equal(before + USDC(1000));
  });

  it("splits rewards pro-rata across multiple depositors", async function () {
    await vault.connect(alice).deposit(0, USDC(1000)); // 25%
    await vault.connect(bob).deposit(0, USDC(3000));   // 75%
    await simulateYield(USDC(400));
    await vault.harvest(0);

    const a = await vault.pendingReward(0, alice.address);
    const b = await vault.pendingReward(0, bob.address);
    // bob ≈ 3× alice
    expect(b).to.be.closeTo(a * 3n, a / 100n);
  });

  it("late depositor earns no past yield", async function () {
    await vault.connect(alice).deposit(0, USDC(1000));
    await simulateYield(USDC(100));
    await vault.harvest(0);

    await vault.connect(bob).deposit(0, USDC(1000)); // joins after harvest
    expect(await vault.pendingReward(0, bob.address)).to.equal(0n);

    await simulateYield(USDC(200));
    await vault.harvest(0);
    // Second batch split 50/50.
    const b = await vault.pendingReward(0, bob.address);
    expect(b).to.be.gt(0n);
  });

  it("harvest with no shares is a no-op", async function () {
    await simulateYield(USDC(50)); // donated before anyone deposits
    await expect(vault.harvest(0)).to.not.be.reverted;
    expect(await vault.strategyCount()).to.equal(1n);
  });

  it("harvest reverts if the asset is delisted from the Market gate", async function () {
    await vault.connect(alice).deposit(0, USDC(1000));
    await simulateYield(USDC(100));
    await tokenGate.delistToken(await usdc.getAddress());
    await expect(vault.harvest(0)).to.be.reverted;
  });

  it("pause blocks deposit/harvest but allows withdraw", async function () {
    await vault.connect(alice).deposit(0, USDC(1000));
    await vault.pause();
    await expect(vault.connect(alice).deposit(0, USDC(1))).to.be.reverted;
    await expect(vault.harvest(0)).to.be.reverted;
    await expect(vault.connect(alice).withdraw(0, USDC(500))).to.not.be.reverted;
  });

  it("supports multiple modular strategies side by side", async function () {
    // A second lowfi yield option over the same asset.
    const MockYieldAdapter = await ethers.getContractFactory("MockYieldAdapter");
    const adapter2 = await MockYieldAdapter.deploy(await usdc.getAddress(), await vault.getAddress(), "Aggressive");
    await adapter2.waitForDeployment();
    await vault.addStrategy(await usdc.getAddress(), await adapter2.getAddress(), "USDC · Aggressive");
    expect(await vault.strategyCount()).to.equal(2n);

    await vault.connect(alice).deposit(1, USDC(500));
    expect(await vault.userShares(1, alice.address)).to.equal(USDC(500));
    expect(await vault.userShares(0, alice.address)).to.equal(0n);
  });
});
