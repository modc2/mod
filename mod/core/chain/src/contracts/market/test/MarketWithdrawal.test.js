const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Market Withdrawal Tests", function () {
  let market, tokenGate, oracle, paymentToken, treasury, owner, user1, user2;
  const INITIAL_SUPPLY = ethers.parseEther("1000000");
  const TOKEN_PRICE = 100000000n; // $1 with 8 decimals
  const PRICE_DECIMALS = 8;
  const MARKET_DECIMALS = 8;
  const PAYMENT_TOKEN_DECIMALS = 18; // default ERC20

  function calcPaymentAmount(stableAmount, price = TOKEN_PRICE) {
    return (stableAmount * 10n**BigInt(PAYMENT_TOKEN_DECIMALS) * 10n**BigInt(PRICE_DECIMALS)) / (price * 10n**BigInt(MARKET_DECIMALS));
  }

  beforeEach(async function () {
    [owner, treasury, user1, user2] = await ethers.getSigners();

    const Token = await ethers.getContractFactory("Token");
    paymentToken = await Token.deploy("Payment Token", "PAY", INITIAL_SUPPLY);
    await paymentToken.waitForDeployment();

    const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
    oracle = await ManualPriceOracle.deploy();
    await oracle.waitForDeployment();

    await oracle.setPrice(await paymentToken.getAddress(), TOKEN_PRICE, PRICE_DECIMALS);

    const TokenGate = await ethers.getContractFactory("TokenGate");
    tokenGate = await TokenGate.deploy(await oracle.getAddress());
    await tokenGate.waitForDeployment();

    await tokenGate.whitelistToken(await paymentToken.getAddress());

    const Market = await ethers.getContractFactory("Market");
    market = await Market.deploy(
      "Stable Token",
      "STABLE",
      treasury.address,
      await tokenGate.getAddress()
    );
    await market.waitForDeployment();
    await market.setCreditFee(0);

    await paymentToken.transfer(user1.address, ethers.parseEther("10000"));
    await paymentToken.transfer(user2.address, ethers.parseEther("10000"));
    await paymentToken.transfer(treasury.address, ethers.parseEther("100000"));
  });

  describe("Immediate Withdrawal", function () {
    const stableAmount = 100000000n; // $1 with 8 decimals

    beforeEach(async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));
    });

    it("Should allow immediate withdrawal without lockup", async function () {
      await expect(market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0))
        .to.emit(market, "Withdrawal");

      expect(await market.balanceOf(user1.address)).to.equal(0);
    });

    it("Should apply withdrawal fee correctly", async function () {
      const userBalanceBefore = await paymentToken.balanceOf(user1.address);
      const paymentAmount = calcPaymentAmount(stableAmount);
      // NO FEE - user gets full amount back
      const expectedReturn = paymentAmount;

      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0);

      const userBalanceAfter = await paymentToken.balanceOf(user1.address);
      expect(userBalanceAfter - userBalanceBefore).to.equal(expectedReturn);
    });
  });

  describe("Multiple Deposits and Withdrawals", function () {
    const stableAmount = 100000000n;

    it("Should handle multiple deposits and immediate withdrawals", async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);

      // First deposit
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));

      // Second deposit
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));

      expect(await market.balanceOf(user1.address)).to.equal(stableAmount * 2n);

      // Withdraw all immediately
      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount * 2n, 0);

      expect(await market.balanceOf(user1.address)).to.equal(0);
    });
  });

  describe("Withdrawal Security", function () {
    const stableAmount = 100000000n;

    beforeEach(async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));
    });

    it("Should require sufficient balance for withdrawal", async function () {
      await expect(
        market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount * 2n, 0)
      ).to.be.revertedWith("Insufficient balance");
    });

    it("Should require whitelisted token for withdrawal", async function () {
      const Token = await ethers.getContractFactory("Token");
      const badToken = await Token.deploy("Bad", "BAD", INITIAL_SUPPLY);
      await badToken.waitForDeployment();

      await expect(
        market.connect(user1).withdraw(await badToken.getAddress(), stableAmount, 0)
      ).to.be.revertedWith("Token not whitelisted");
    });

    it("Should burn stable tokens on withdrawal", async function () {
      const balanceBefore = await market.balanceOf(user1.address);

      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0);

      const balanceAfter = await market.balanceOf(user1.address);
      expect(balanceAfter).to.equal(balanceBefore - stableAmount);
    });

    it("Should transfer correct payment amount from Market contract", async function () {
      const marketBalanceBefore = await paymentToken.balanceOf(await market.getAddress());
      const userBalanceBefore = await paymentToken.balanceOf(user1.address);

      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0);

      const paymentAmount = calcPaymentAmount(stableAmount);
      // NO FEE - full amount returned
      const expectedReturn = paymentAmount;

      expect(await paymentToken.balanceOf(await market.getAddress())).to.equal(marketBalanceBefore - paymentAmount);
      expect(await paymentToken.balanceOf(user1.address)).to.equal(userBalanceBefore + expectedReturn);
    });
  });

  describe("Edge Cases", function () {
    const stableAmount = 100000000n;

    it("Should handle zero withdrawal amount", async function () {
      await expect(
        market.connect(user1).withdraw(await paymentToken.getAddress(), 0, 0)
      ).to.be.revertedWith("Invalid amount");
    });

    it("Should handle withdrawal with dynamic price changes", async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));

      // Change price
      const newPrice = 200000000n; // $2
      await oracle.setPrice(await paymentToken.getAddress(), newPrice, PRICE_DECIMALS);

      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0);

      // Should use new price for withdrawal
      expect(await market.balanceOf(user1.address)).to.equal(0);
    });

    it("Should handle multiple users with immediate withdrawals", async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);

      // User1 deposits
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));

      // User2 deposits
      await paymentToken.connect(user2).approve(await market.getAddress(), paymentAmount);
      await market.connect(user2).credit(await paymentToken.getAddress(), stableAmount, calcPaymentAmount(stableAmount));

      // Both can withdraw immediately
      await expect(market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0))
        .to.emit(market, "Withdrawal");

      await expect(market.connect(user2).withdraw(await paymentToken.getAddress(), stableAmount, 0))
        .to.emit(market, "Withdrawal");
    });
  });
});
