const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Market Integration", function () {
  let market, debit, tokenGate, oracle, paymentToken, treasury, owner, user1, provider;
  const INITIAL_SUPPLY = ethers.parseEther("1000000");
  const TOKEN_PRICE = 100000000n; // $1.00 with 8 decimals
  const PRICE_DECIMALS = 8;
  const MARKET_DECIMALS = 8;
  const PAYMENT_TOKEN_DECIMALS = 18; // default ERC20

  const DEBIT_TYPE = {
    DebitAuthorization: [
      { name: "client", type: "address" },
      { name: "provider", type: "address" },
      { name: "amount", type: "uint256" },
      { name: "nonce", type: "uint256" },
      { name: "deadline", type: "uint256" },
    ],
  };

  function calcPaymentAmount(stableAmount) {
    return (stableAmount * 10n**BigInt(PAYMENT_TOKEN_DECIMALS) * 10n**BigInt(PRICE_DECIMALS)) / (TOKEN_PRICE * 10n**BigInt(MARKET_DECIMALS));
  }

  async function getDomain() {
    return {
      name: "MarketDebit",
      version: "1",
      chainId: (await ethers.provider.getNetwork()).chainId,
      verifyingContract: await debit.getAddress(),
    };
  }

  async function signDebit(signer, providerAddr, amount, nonce, deadline) {
    const domain = await getDomain();
    const value = {
      client: signer.address,
      provider: providerAddr,
      amount: amount,
      nonce: nonce,
      deadline: deadline,
    };
    return await signer.signTypedData(domain, DEBIT_TYPE, value);
  }

  beforeEach(async function () {
    [owner, treasury, user1, provider] = await ethers.getSigners();

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

    const Debit = await ethers.getContractFactory("Debit");
    debit = await Debit.deploy(await market.getAddress());
    await debit.waitForDeployment();
    await market.setDebitContract(await debit.getAddress());
    await market.setCreditFee(0);

    await paymentToken.transfer(user1.address, ethers.parseEther("10000"));
    await paymentToken.transfer(treasury.address, ethers.parseEther("100000"));
  });

  describe("TokenGate Integration", function () {
    it("Should set tokenGate correctly", async function () {
      expect(await market.tokenGate()).to.equal(await tokenGate.getAddress());
    });

    it("Should update tokenGate", async function () {
      const TokenGate = await ethers.getContractFactory("TokenGate");
      const newTokenGate = await TokenGate.deploy(await oracle.getAddress());
      await newTokenGate.waitForDeployment();

      await expect(market.setTokenGate(await newTokenGate.getAddress()))
        .to.emit(market, "TokenGateUpdated");

      expect(await market.tokenGate()).to.equal(await newTokenGate.getAddress());
    });
  });

  describe("Token Whitelist via TokenGate", function () {
    it("Should check token whitelist via TokenGate", async function () {
      expect(await market.isTokenWhitelisted(await paymentToken.getAddress())).to.be.true;
    });

    it("Should reject non-whitelisted token", async function () {
      const Token = await ethers.getContractFactory("Token");
      const badToken = await Token.deploy("Bad", "BAD", INITIAL_SUPPLY);
      await badToken.waitForDeployment();

      expect(await market.isTokenWhitelisted(await badToken.getAddress())).to.be.false;
    });

    it("Should get token price from TokenGate oracle", async function () {
      const [price, decimals] = await market.getTokenPrice(await paymentToken.getAddress());
      expect(price).to.equal(TOKEN_PRICE);
      expect(decimals).to.equal(PRICE_DECIMALS);
    });
  });

  describe("Credit with TokenGate Pricing", function () {
    const stableAmount = 10000000000n; // $100 in 8 decimals

    it("Should credit with TokenGate oracle-based pricing", async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);

      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);

      await expect(market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, paymentAmount))
        .to.emit(market, "Credit");

      expect(await market.balanceOf(user1.address)).to.equal(stableAmount);
    });

    it("Should handle dynamic oracle price updates via TokenGate", async function () {
      const newPrice = 200000000n; // $2
      await oracle.setPrice(await paymentToken.getAddress(), newPrice, PRICE_DECIMALS);

      const paymentAmount = (stableAmount * 10n**BigInt(PAYMENT_TOKEN_DECIMALS) * 10n**BigInt(PRICE_DECIMALS)) / (newPrice * 10n**BigInt(MARKET_DECIMALS));

      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, paymentAmount);

      expect(await market.balanceOf(user1.address)).to.equal(stableAmount);
    });

    it("Should reject credit with non-whitelisted token", async function () {
      const Token = await ethers.getContractFactory("Token");
      const badToken = await Token.deploy("Bad", "BAD", INITIAL_SUPPLY);
      await badToken.waitForDeployment();

      await expect(
        market.connect(user1).credit(await badToken.getAddress(), stableAmount, ethers.parseEther("1000"))
      ).to.be.revertedWith("Token not whitelisted");
    });
  });

  describe("Debit via Debit Contract with EIP-712 Signatures", function () {
    const stableAmount = 10000000000n;

    beforeEach(async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, paymentAmount);
    });

    it("Should debit from client and credit provider with 5% treasury fee", async function () {
      const treasuryFee = (stableAmount * 5n) / 100n;
      const providerAmount = stableAmount - treasuryFee;
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);

      const sig = await signDebit(user1, provider.address, stableAmount, 0n, deadline);

      await expect(
        debit.executeDebit(user1.address, provider.address, stableAmount, deadline, sig)
      ).to.emit(debit, "DebitExecuted");

      expect(await market.balanceOf(user1.address)).to.equal(0);
      expect(await market.balanceOf(provider.address)).to.equal(providerAmount);
      expect(await market.balanceOf(treasury.address)).to.equal(treasuryFee);
    });

    it("Should reject debit with invalid signature (wrong signer)", async function () {
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);
      // provider signs instead of user1
      const sig = await signDebit(provider, provider.address, stableAmount, 0n, deadline);

      await expect(
        debit.executeDebit(user1.address, provider.address, stableAmount, deadline, sig)
      ).to.be.revertedWith("Invalid signature");
    });

    it("Should reject debit with insufficient balance", async function () {
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);
      const sig = await signDebit(user1, provider.address, stableAmount * 2n, 0n, deadline);

      await expect(
        debit.executeDebit(user1.address, provider.address, stableAmount * 2n, deadline, sig)
      ).to.be.revertedWith("Insufficient balance");
    });

    it("Should reject debit with invalid client", async function () {
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);
      const sig = await signDebit(user1, provider.address, stableAmount, 0n, deadline);

      await expect(
        debit.executeDebit(ethers.ZeroAddress, provider.address, stableAmount, deadline, sig)
      ).to.be.revertedWith("Invalid client");
    });

    it("Should reject debit with invalid provider", async function () {
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);
      const sig = await signDebit(user1, ethers.ZeroAddress, stableAmount, 0n, deadline);

      await expect(
        debit.executeDebit(user1.address, ethers.ZeroAddress, stableAmount, deadline, sig)
      ).to.be.revertedWith("Invalid provider");
    });

    it("Should calculate correct treasury fee percentage", async function () {
      const testAmount = 1000000000n;
      const expectedFee = (testAmount * 5n) / 100n;
      const expectedProvider = testAmount - expectedFee;
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);

      const sig = await signDebit(user1, provider.address, testAmount, 0n, deadline);
      await debit.executeDebit(user1.address, provider.address, testAmount, deadline, sig);

      expect(await market.balanceOf(treasury.address)).to.equal(expectedFee);
      expect(await market.balanceOf(provider.address)).to.equal(expectedProvider);
    });

    it("Should track total treasury fees accrued correctly", async function () {
      const treasuryFee = (stableAmount * 5n) / 100n;
      const totalFeesBefore = await market.totalTreasuryFeesAccrued();
      const block = await ethers.provider.getBlock("latest");
      const deadline = BigInt(block.timestamp + 3600);

      const sig = await signDebit(user1, provider.address, stableAmount, 0n, deadline);
      await debit.executeDebit(user1.address, provider.address, stableAmount, deadline, sig);

      const totalFeesAfter = await market.totalTreasuryFeesAccrued();
      expect(totalFeesAfter).to.equal(totalFeesBefore + treasuryFee);
    });

    it("Should verify Market contract holds deposited tokens, not treasury", async function () {
      const marketBalance = await paymentToken.balanceOf(await market.getAddress());
      const paymentAmount = calcPaymentAmount(stableAmount);

      expect(marketBalance).to.be.gte(paymentAmount);
    });
  });

  describe("Withdrawal Tests", function () {
    const stableAmount = 10000000000n;

    beforeEach(async function () {
      const paymentAmount = calcPaymentAmount(stableAmount);
      await paymentToken.connect(user1).approve(await market.getAddress(), paymentAmount);
      await market.connect(user1).credit(await paymentToken.getAddress(), stableAmount, paymentAmount);
    });

    it("Should allow immediate withdrawal without lockup", async function () {
      await expect(market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0))
        .to.emit(market, "Withdrawal");

      expect(await market.balanceOf(user1.address)).to.equal(0);
    });

    it("Should return full amount with NO withdrawal fee", async function () {
      const userBalanceBefore = await paymentToken.balanceOf(user1.address);
      const paymentAmount = calcPaymentAmount(stableAmount);

      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0);

      const userBalanceAfter = await paymentToken.balanceOf(user1.address);

      expect(userBalanceAfter - userBalanceBefore).to.equal(paymentAmount);
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

    it("Should handle withdrawal with dynamic price changes", async function () {
      const newPrice = 200000000n;
      await oracle.setPrice(await paymentToken.getAddress(), newPrice, PRICE_DECIMALS);

      await market.connect(user1).withdraw(await paymentToken.getAddress(), stableAmount, 0);

      expect(await market.balanceOf(user1.address)).to.equal(0);
    });

    it("Should handle zero withdrawal amount", async function () {
      await expect(
        market.connect(user1).withdraw(await paymentToken.getAddress(), 0, 0)
      ).to.be.revertedWith("Invalid amount");
    });
  });
});
