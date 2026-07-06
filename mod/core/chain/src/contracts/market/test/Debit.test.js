const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Debit Contract - EIP-712 Signed Debits", function () {
  let market, debit, tokenGate, oracle, paymentToken;
  let owner, treasury, client, provider, attacker;
  const INITIAL_SUPPLY = ethers.parseEther("1000000");
  const TOKEN_PRICE = 100000000n; // $1 with 8 decimals
  const PRICE_DECIMALS = 8;

  // EIP-712 domain and type
  const DEBIT_TYPE = {
    DebitAuthorization: [
      { name: "client", type: "address" },
      { name: "provider", type: "address" },
      { name: "amount", type: "uint256" },
      { name: "nonce", type: "uint256" },
      { name: "deadline", type: "uint256" },
    ],
  };

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
    const signature = await signer.signTypedData(domain, DEBIT_TYPE, value);
    return signature;
  }

  function calcPaymentAmount(stableAmount) {
    return (stableAmount * 10n ** 18n * 10n ** BigInt(PRICE_DECIMALS)) / (TOKEN_PRICE * 10n ** 8n);
  }

  beforeEach(async function () {
    [owner, treasury, client, provider, attacker] = await ethers.getSigners();

    // Deploy payment token
    const Token = await ethers.getContractFactory("Token");
    paymentToken = await Token.deploy("Payment Token", "PAY", INITIAL_SUPPLY);
    await paymentToken.waitForDeployment();

    // Deploy oracle + set price
    const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
    oracle = await ManualPriceOracle.deploy();
    await oracle.waitForDeployment();
    await oracle.setPrice(await paymentToken.getAddress(), TOKEN_PRICE, PRICE_DECIMALS);

    // Deploy TokenGate + whitelist
    const TokenGate = await ethers.getContractFactory("TokenGate");
    tokenGate = await TokenGate.deploy(await oracle.getAddress());
    await tokenGate.waitForDeployment();
    await tokenGate.whitelistToken(await paymentToken.getAddress());

    // Deploy Market
    const Market = await ethers.getContractFactory("Market");
    market = await Market.deploy(
      "Stable Token",
      "STABLE",
      treasury.address,
      await tokenGate.getAddress()
    );
    await market.waitForDeployment();

    // Deploy Debit
    const Debit = await ethers.getContractFactory("Debit");
    debit = await Debit.deploy(await market.getAddress());
    await debit.waitForDeployment();

    // Authorize Debit on Market
    await market.setDebitContract(await debit.getAddress());

    // Fund client with payment tokens and credit them on Market
    await paymentToken.transfer(client.address, ethers.parseEther("10000"));
    const creditAmount = 1000_00000000n; // $1000 in 8 decimals
    const paymentAmount = calcPaymentAmount(creditAmount);
    await paymentToken.connect(client).approve(await market.getAddress(), paymentAmount);
    await market.connect(client).credit(await paymentToken.getAddress(), creditAmount, paymentAmount);
  });

  describe("Successful Debit", function () {
    it("Should execute debit with valid client signature", async function () {
      const amount = 100_00000000n; // $100
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      const clientBalBefore = await market.balanceOf(client.address);
      const providerBalBefore = await market.balanceOf(provider.address);
      const treasuryBalBefore = await market.balanceOf(treasury.address);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.emit(debit, "DebitExecuted");

      const treasuryFee = (amount * 5n) / 100n;
      const providerAmount = amount - treasuryFee;

      expect(await market.balanceOf(client.address)).to.equal(clientBalBefore - amount);
      expect(await market.balanceOf(provider.address)).to.equal(providerBalBefore + providerAmount);
      expect(await market.balanceOf(treasury.address)).to.equal(treasuryBalBefore + treasuryFee);
    });

    it("Should allow anyone to submit a valid signature (not just owner)", async function () {
      const amount = 50_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      // Attacker submits the signature — should work since signature is valid
      await expect(
        debit.connect(attacker).executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.emit(debit, "DebitExecuted");
    });

    it("Should track treasury fees on Market", async function () {
      const amount = 200_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, signature);

      const expectedFee = (amount * 5n) / 100n;
      expect(await market.totalTreasuryFeesAccrued()).to.equal(expectedFee);
    });
  });

  describe("Signature Validation", function () {
    it("Should reject expired deadline", async function () {
      const amount = 100_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) - 100); // expired

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.be.revertedWith("Signature expired");
    });

    it("Should reject wrong signer", async function () {
      const amount = 100_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      // Attacker signs instead of client
      const signature = await signDebit(attacker, provider.address, amount, nonce, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.be.revertedWith("Invalid signature");
    });

    it("Should reject wrong amount in signature", async function () {
      const amount = 100_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      // Sign for 100, try to execute for 200
      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, 200_00000000n, deadline, signature)
      ).to.be.revertedWith("Invalid signature");
    });

    it("Should reject wrong provider in signature", async function () {
      const amount = 100_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      // Try to redirect to attacker
      await expect(
        debit.executeDebit(client.address, attacker.address, amount, deadline, signature)
      ).to.be.revertedWith("Invalid signature");
    });

    it("Should reject insufficient balance", async function () {
      const amount = 2000_00000000n; // More than client has
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.be.revertedWith("Insufficient balance");
    });
  });

  describe("Nonce / Replay Protection", function () {
    it("Should increment nonce after execution", async function () {
      expect(await debit.getNonce(client.address)).to.equal(0);

      const amount = 50_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, signature);

      expect(await debit.getNonce(client.address)).to.equal(1);
    });

    it("Should reject replayed signature (same nonce)", async function () {
      const amount = 50_00000000n;
      const nonce = 0n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, nonce, deadline);

      // First execution succeeds
      await debit.executeDebit(client.address, provider.address, amount, deadline, signature);

      // Replay with same signature fails (nonce is now 1)
      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.be.revertedWith("Invalid signature");
    });

    it("Should accept signature with correct incremented nonce", async function () {
      const amount = 50_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      // First debit at nonce 0
      const sig1 = await signDebit(client, provider.address, amount, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, sig1);

      // Second debit at nonce 1
      const sig2 = await signDebit(client, provider.address, amount, 1n, deadline);
      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, sig2)
      ).to.emit(debit, "DebitExecuted");

      expect(await debit.getNonce(client.address)).to.equal(2);
    });
  });

  describe("Temporal Cache", function () {
    it("Should cache the most recent debit per client->provider", async function () {
      const amount = 100_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      const signature = await signDebit(client, provider.address, amount, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, signature);

      const [cachedAmount, cachedTimestamp, cachedNonce] = await debit.getLastDebit(client.address, provider.address);

      expect(cachedAmount).to.equal(amount);
      expect(cachedTimestamp).to.be.gt(0);
      expect(cachedNonce).to.equal(0);
    });

    it("Should update cache on subsequent debits to same provider", async function () {
      const amount1 = 50_00000000n;
      const amount2 = 75_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      // First debit
      const sig1 = await signDebit(client, provider.address, amount1, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount1, deadline, sig1);

      const [cached1Amount] = await debit.getLastDebit(client.address, provider.address);
      expect(cached1Amount).to.equal(amount1);

      // Second debit overwrites cache
      const sig2 = await signDebit(client, provider.address, amount2, 1n, deadline);
      await debit.executeDebit(client.address, provider.address, amount2, deadline, sig2);

      const [cached2Amount, , cached2Nonce] = await debit.getLastDebit(client.address, provider.address);
      expect(cached2Amount).to.equal(amount2);
      expect(cached2Nonce).to.equal(1);
    });

    it("Should maintain separate caches for different providers", async function () {
      const amount = 50_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

      // Debit to provider
      const sig1 = await signDebit(client, provider.address, amount, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, sig1);

      // Debit to attacker (used as second provider here)
      const sig2 = await signDebit(client, attacker.address, amount, 1n, deadline);
      await debit.executeDebit(client.address, attacker.address, amount, deadline, sig2);

      const [providerAmount] = await debit.getLastDebit(client.address, provider.address);
      const [attackerAmount] = await debit.getLastDebit(client.address, attacker.address);

      expect(providerAmount).to.equal(amount);
      expect(attackerAmount).to.equal(amount);
    });
  });

  describe("Market Authorization", function () {
    it("Should reject debitBurn from non-debit contract", async function () {
      await expect(
        market.connect(attacker).debitBurn(client.address, 100n)
      ).to.be.revertedWith("Only debit contract");
    });

    it("Should reject debitMint from non-debit contract", async function () {
      await expect(
        market.connect(attacker).debitMint(attacker.address, 100n)
      ).to.be.revertedWith("Only debit contract");
    });

    it("Should reject addTreasuryFees from non-debit contract", async function () {
      await expect(
        market.connect(attacker).addTreasuryFees(100n)
      ).to.be.revertedWith("Only debit contract");
    });
  });

  describe("Market Security Fixes", function () {
    it("Should reject credit when paused", async function () {
      await market.pause();

      const amount = 100_00000000n;
      const paymentAmount = calcPaymentAmount(amount);
      await paymentToken.connect(client).approve(await market.getAddress(), paymentAmount);

      await expect(
        market.connect(client).credit(await paymentToken.getAddress(), amount, paymentAmount)
      ).to.be.revertedWith("Pausable: paused");
    });

    it("Should reject withdraw when paused", async function () {
      await market.pause();

      await expect(
        market.connect(client).withdraw(await paymentToken.getAddress(), 100_00000000n, 0)
      ).to.be.revertedWith("Pausable: paused");
    });

    it("Should enforce slippage protection on credit", async function () {
      const amount = 100_00000000n;
      const tooLowMax = 1n; // way too low

      await paymentToken.connect(client).approve(await market.getAddress(), ethers.parseEther("1000"));

      await expect(
        market.connect(client).credit(await paymentToken.getAddress(), amount, tooLowMax)
      ).to.be.revertedWith("Exceeds max payment");
    });

    it("Should enforce slippage protection on withdraw", async function () {
      const tooHighMin = ethers.parseEther("999999"); // way too high

      await expect(
        market.connect(client).withdraw(await paymentToken.getAddress(), 100_00000000n, tooHighMin)
      ).to.be.revertedWith("Below min receive");
    });
  });

  describe("Unsigned Debit (Backward Compatible)", function () {
    it("Should allow Market.debit() owner-only unsigned path", async function () {
      const amount = 100_00000000n;

      const clientBalBefore = await market.balanceOf(client.address);
      const providerBalBefore = await market.balanceOf(provider.address);
      const treasuryBalBefore = await market.balanceOf(treasury.address);

      await expect(
        market.connect(owner).debit(client.address, provider.address, amount, 0, "0x")
      ).to.emit(market, "Debit");

      const treasuryFee = (amount * 5n) / 100n;
      const providerAmount = amount - treasuryFee;

      expect(await market.balanceOf(client.address)).to.equal(clientBalBefore - amount);
      expect(await market.balanceOf(provider.address)).to.equal(providerBalBefore + providerAmount);
      expect(await market.balanceOf(treasury.address)).to.equal(treasuryBalBefore + treasuryFee);
    });

    it("Should execute Market.debit() with signed path when deadline is non-zero", async function () {
      const amount = 100_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const signature = await signDebit(client, provider.address, amount, 0n, deadline);

      const clientBalBefore = await market.balanceOf(client.address);
      const providerBalBefore = await market.balanceOf(provider.address);
      const treasuryBalBefore = await market.balanceOf(treasury.address);

      await expect(
        market.connect(owner).debit(client.address, provider.address, amount, deadline, signature)
      ).to.emit(market, "Debit");

      const treasuryFee = (amount * 5n) / 100n;
      const providerAmount = amount - treasuryFee;

      expect(await market.balanceOf(client.address)).to.equal(clientBalBefore - amount);
      expect(await market.balanceOf(provider.address)).to.equal(providerBalBefore + providerAmount);
      expect(await market.balanceOf(treasury.address)).to.equal(treasuryBalBefore + treasuryFee);
    });

    it("Should reject Market.debit() from non-owner", async function () {
      await expect(
        market.connect(attacker).debit(client.address, provider.address, 100_00000000n, 0, "0x")
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });

    it("Should allow Debit owner to call executeDebitUnsigned directly", async function () {
      const amount = 50_00000000n;

      await expect(
        debit.connect(owner).executeDebitUnsigned(client.address, provider.address, amount)
      ).to.emit(debit, "DebitExecuted");
    });

    it("Should reject executeDebitUnsigned from unauthorized caller", async function () {
      await expect(
        debit.connect(attacker).executeDebitUnsigned(client.address, provider.address, 100_00000000n)
      ).to.be.revertedWith("Unauthorized");
    });

    it("Should cache transactions from unsigned path", async function () {
      const amount = 100_00000000n;
      await market.connect(owner).debit(client.address, provider.address, amount, 0, "0x");

      const [cachedAmount, cachedTimestamp, cachedNonce] = await debit.getLastDebit(client.address, provider.address);
      expect(cachedAmount).to.equal(amount);
      expect(cachedTimestamp).to.be.gt(0);
      expect(cachedNonce).to.equal(0);
    });

    it("Should increment nonces from unsigned path", async function () {
      expect(await debit.getNonce(client.address)).to.equal(0);

      await market.connect(owner).debit(client.address, provider.address, 50_00000000n, 0, "0x");
      expect(await debit.getNonce(client.address)).to.equal(1);

      await market.connect(owner).debit(client.address, provider.address, 50_00000000n, 0, "0x");
      expect(await debit.getNonce(client.address)).to.equal(2);
    });
  });

  describe("Signature Requirement Toggle", function () {
    it("Should default to signatureRequired = false", async function () {
      expect(await debit.signatureRequired()).to.equal(false);
    });

    it("Should allow owner to enable signature requirement", async function () {
      await expect(debit.setSignatureRequired(true))
        .to.emit(debit, "SignatureRequirementUpdated")
        .withArgs(true);

      expect(await debit.signatureRequired()).to.equal(true);
    });

    it("Should block unsigned debits when signatures required", async function () {
      await debit.setSignatureRequired(true);

      await expect(
        market.connect(owner).debit(client.address, provider.address, 100_00000000n, 0, "0x")
      ).to.be.revertedWith("Signatures required");
    });

    it("Should still allow signed debits when signatures required", async function () {
      await debit.setSignatureRequired(true);

      const amount = 100_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const signature = await signDebit(client, provider.address, amount, 0n, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.emit(debit, "DebitExecuted");
    });

    it("Should allow re-enabling unsigned debits", async function () {
      await debit.setSignatureRequired(true);
      await debit.setSignatureRequired(false);

      await expect(
        market.connect(owner).debit(client.address, provider.address, 50_00000000n, 0, "0x")
      ).to.emit(debit, "DebitExecuted");
    });

    it("Should reject non-owner from toggling signature requirement", async function () {
      await expect(
        debit.connect(attacker).setSignatureRequired(true)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });
  });

  describe("Daily Spending Limit", function () {
    it("Should default to 1000 (8 decimals) daily limit", async function () {
      const limit = await debit.getEffectiveDailyLimit(client.address);
      expect(limit).to.equal(1000_00000000n);
    });

    it("Should allow client to set their own daily limit", async function () {
      await expect(debit.connect(client).setDailyLimit(500_00000000n))
        .to.emit(debit, "DailyLimitUpdated")
        .withArgs(client.address, 500_00000000n);

      expect(await debit.getEffectiveDailyLimit(client.address)).to.equal(500_00000000n);
    });

    it("Should reject daily limit of 0", async function () {
      await expect(
        debit.connect(client).setDailyLimit(0)
      ).to.be.revertedWith("Limit must be > 0");
    });

    it("Should allow client to update their daily limit", async function () {
      await debit.connect(client).setDailyLimit(500_00000000n);
      expect(await debit.getEffectiveDailyLimit(client.address)).to.equal(500_00000000n);

      await debit.connect(client).setDailyLimit(2000_00000000n);
      expect(await debit.getEffectiveDailyLimit(client.address)).to.equal(2000_00000000n);
    });

    it("Should block debit that exceeds daily limit", async function () {
      // Set limit to $200
      await debit.connect(client).setDailyLimit(200_00000000n);

      const amount = 201_00000000n; // $201 — over limit
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const signature = await signDebit(client, provider.address, amount, 0n, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.be.revertedWith("Daily spending limit exceeded");
    });

    it("Should block debit when cumulative daily spending exceeds limit", async function () {
      // Set limit to $150
      await debit.connect(client).setDailyLimit(150_00000000n);

      const amount1 = 100_00000000n; // $100
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const sig1 = await signDebit(client, provider.address, amount1, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount1, deadline, sig1);

      // Second debit of $60 should fail (total $160 > $150 limit)
      const amount2 = 60_00000000n;
      const sig2 = await signDebit(client, provider.address, amount2, 1n, deadline);
      await expect(
        debit.executeDebit(client.address, provider.address, amount2, deadline, sig2)
      ).to.be.revertedWith("Daily spending limit exceeded");
    });

    it("Should allow spending up to exact daily limit", async function () {
      await debit.connect(client).setDailyLimit(200_00000000n);

      const amount = 200_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const signature = await signDebit(client, provider.address, amount, 0n, deadline);

      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, signature)
      ).to.emit(debit, "DebitExecuted");
    });

    it("Should enforce daily limit on unsigned debits too", async function () {
      await debit.connect(client).setDailyLimit(200_00000000n);

      const amount = 201_00000000n;
      await expect(
        market.connect(owner).debit(client.address, provider.address, amount, 0, "0x")
      ).to.be.revertedWith("Daily spending limit exceeded");
    });

    it("Should track daily spent correctly", async function () {
      const amount = 100_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const signature = await signDebit(client, provider.address, amount, 0n, deadline);

      await debit.executeDebit(client.address, provider.address, amount, deadline, signature);

      expect(await debit.getDailySpent(client.address)).to.equal(amount);
    });

    it("Should report remaining daily allowance", async function () {
      await debit.connect(client).setDailyLimit(500_00000000n);

      const amount = 200_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);
      const signature = await signDebit(client, provider.address, amount, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, signature);

      expect(await debit.getDailyRemaining(client.address)).to.equal(300_00000000n);
    });

    it("Should reset daily spending on a new day", async function () {
      // Client has 990 tokens (1000 credited minus 1% fee)
      // Use amounts that fit within the client's balance across both days
      const amount = 400_00000000n;
      const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600 * 48);
      const sig1 = await signDebit(client, provider.address, amount, 0n, deadline);
      await debit.executeDebit(client.address, provider.address, amount, deadline, sig1);

      expect(await debit.getDailySpent(client.address)).to.equal(amount);

      // Advance time by 1 day
      await ethers.provider.send("evm_increaseTime", [86400]);
      await ethers.provider.send("evm_mine", []);

      // Daily spent should be 0 on the new day
      expect(await debit.getDailySpent(client.address)).to.equal(0);

      // Should be able to spend again (client has 990 - 400 = 590 remaining balance)
      const sig2 = await signDebit(client, provider.address, amount, 1n, deadline);
      await expect(
        debit.executeDebit(client.address, provider.address, amount, deadline, sig2)
      ).to.emit(debit, "DebitExecuted");
    });

    it("Should have independent limits per client", async function () {
      await debit.connect(client).setDailyLimit(200_00000000n);

      // Provider's limit should still be default
      expect(await debit.getEffectiveDailyLimit(provider.address)).to.equal(1000_00000000n);
      expect(await debit.getEffectiveDailyLimit(client.address)).to.equal(200_00000000n);
    });
  });

  describe("View Functions", function () {
    it("Should return domain separator", async function () {
      const domainSep = await debit.getDomainSeparator();
      expect(domainSep).to.not.equal(ethers.ZeroHash);
    });

    it("Should return correct nonce", async function () {
      expect(await debit.getNonce(client.address)).to.equal(0);
      expect(await debit.getNonce(provider.address)).to.equal(0);
    });

    it("Should return empty cache for non-existent pair", async function () {
      const [amount, timestamp, nonce] = await debit.getLastDebit(client.address, provider.address);
      expect(amount).to.equal(0);
      expect(timestamp).to.equal(0);
      expect(nonce).to.equal(0);
    });
  });
});
