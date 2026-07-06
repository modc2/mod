const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Treasury", function () {
  let treasury, tokenGate, oracle, governanceToken, token1, token2, owner, user1, user2;
  const INITIAL_SUPPLY = ethers.parseEther("1000000");
  const OWNER_PERCENTAGE = 2000; // 20%
  const TOKEN_PRICE = 100000000n; // $1 with 8 decimals

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const Token = await ethers.getContractFactory("Token");
    governanceToken = await Token.deploy("Governance", "GOV", INITIAL_SUPPLY);
    await governanceToken.waitForDeployment();

    token1 = await Token.deploy("Token1", "TK1", INITIAL_SUPPLY);
    await token1.waitForDeployment();

    token2 = await Token.deploy("Token2", "TK2", INITIAL_SUPPLY);
    await token2.waitForDeployment();

    // Deploy oracle
    const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
    oracle = await ManualPriceOracle.deploy();
    await oracle.waitForDeployment();
    await oracle.setPrice(await token1.getAddress(), TOKEN_PRICE, 8);
    await oracle.setPrice(await token2.getAddress(), TOKEN_PRICE, 8);

    // Deploy TokenGate
    const TokenGate = await ethers.getContractFactory("TokenGate");
    tokenGate = await TokenGate.deploy(await oracle.getAddress());
    await tokenGate.waitForDeployment();
    await tokenGate.whitelistToken(await token1.getAddress());
    await tokenGate.whitelistToken(await token2.getAddress());

    // Deploy Treasury with TokenGate
    const Treasury = await ethers.getContractFactory("Treasury");
    treasury = await Treasury.deploy(OWNER_PERCENTAGE, await tokenGate.getAddress());
    await treasury.waitForDeployment();

    await treasury.setGovernanceToken(await governanceToken.getAddress());

    await governanceToken.transfer(user1.address, ethers.parseEther("200000"));
    await governanceToken.transfer(user2.address, ethers.parseEther("300000"));
  });

  describe("Deployment", function () {
    it("Should set correct owner percentage", async function () {
      expect(await treasury.ownerPercentage()).to.equal(OWNER_PERCENTAGE);
    });

    it("Should set governance token", async function () {
      expect(await treasury.governanceToken()).to.equal(await governanceToken.getAddress());
    });

    it("Should set TokenGate", async function () {
      expect(await treasury.tokenGate()).to.equal(await tokenGate.getAddress());
    });

    it("Should get whitelisted tokens from TokenGate", async function () {
      const tokens = await treasury.getTreasuryTokens();
      expect(tokens.length).to.equal(2);
      expect(tokens[0]).to.equal(await token1.getAddress());
      expect(tokens[1]).to.equal(await token2.getAddress());
    });
  });

  describe("Funding", function () {
    const fundAmount = ethers.parseEther("1000");

    it("Should fund treasury with whitelisted token", async function () {
      await token1.approve(await treasury.getAddress(), fundAmount);
      
      await expect(treasury.fundTreasury(await token1.getAddress(), fundAmount))
        .to.emit(treasury, "TreasuryFunded");

      expect(await token1.balanceOf(await treasury.getAddress())).to.equal(fundAmount);
    });

    it("Should reject non-whitelisted token", async function () {
      const Token = await ethers.getContractFactory("Token");
      const badToken = await Token.deploy("Bad", "BAD", INITIAL_SUPPLY);
      
      await expect(
        treasury.fundTreasury(await badToken.getAddress(), fundAmount)
      ).to.be.revertedWith("Token not whitelisted");
    });
  });

  describe("Owner Withdrawal", function () {
    const fundAmount = ethers.parseEther("1000");

    beforeEach(async function () {
      await token1.approve(await treasury.getAddress(), fundAmount);
      await treasury.fundTreasury(await token1.getAddress(), fundAmount);
    });

    it("Should calculate owner claimable correctly", async function () {
      const claimable = await treasury.getOwnerClaimableAmount(await token1.getAddress());
      const expected = (fundAmount * BigInt(OWNER_PERCENTAGE)) / 10000n;
      expect(claimable).to.equal(expected);
    });

    it("Should allow owner to withdraw", async function () {
      await expect(treasury.ownerWithdraw(await token1.getAddress()))
        .to.emit(treasury, "OwnerWithdrawn");

      const claimed = await treasury.ownerClaimed(await token1.getAddress());
      expect(claimed).to.be.gt(0);
    });
  });

  describe("Holder Withdrawal", function () {
    const fundAmount = ethers.parseEther("1000");

    beforeEach(async function () {
      await token1.approve(await treasury.getAddress(), fundAmount);
      await treasury.fundTreasury(await token1.getAddress(), fundAmount);
    });

    it("Should calculate claimable amount correctly", async function () {
      const claimable = await treasury.getClaimableAmount(user1.address, await token1.getAddress());
      expect(claimable).to.be.gt(0);
    });

    it("Should allow holder to withdraw", async function () {
      await expect(treasury.connect(user1).withdrawToken(await token1.getAddress()))
        .to.emit(treasury, "Withdrawn");

      const claimed = await treasury.claimed(user1.address, await token1.getAddress());
      expect(claimed).to.be.gt(0);
    });

    it("Should withdraw all whitelisted tokens", async function () {
      await token2.approve(await treasury.getAddress(), fundAmount);
      await treasury.fundTreasury(await token2.getAddress(), fundAmount);

      await treasury.connect(user1).withdrawAll();

      const claimed1 = await treasury.claimed(user1.address, await token1.getAddress());
      const claimed2 = await treasury.claimed(user1.address, await token2.getAddress());
      
      expect(claimed1).to.be.gt(0);
      expect(claimed2).to.be.gt(0);
    });
  });

  describe("Proportional Distribution", function () {
    const fundAmount = ethers.parseEther("1000");

    beforeEach(async function () {
      await token1.approve(await treasury.getAddress(), fundAmount);
      await treasury.fundTreasury(await token1.getAddress(), fundAmount);
    });

    it("Should distribute proportionally to holders", async function () {
      const claimable1 = await treasury.getClaimableAmount(user1.address, await token1.getAddress());
      const claimable2 = await treasury.getClaimableAmount(user2.address, await token1.getAddress());

      const balance1 = await governanceToken.balanceOf(user1.address);
      const balance2 = await governanceToken.balanceOf(user2.address);

      // user1: 200000, user2: 300000 -> ratio should be 2:3
      const leftSide = claimable1 * balance2;
      const rightSide = claimable2 * balance1;
      
      const ratio = Number(leftSide * 10000n / rightSide);
      expect(ratio).to.be.closeTo(10000, 100);
    });
  });

  describe("TokenGate Integration", function () {
    it("Should sync with TokenGate whitelist changes", async function () {
      // Initially 2 tokens
      let tokens = await treasury.getTreasuryTokens();
      expect(tokens.length).to.equal(2);

      // Add new token to TokenGate
      const Token = await ethers.getContractFactory("Token");
      const token3 = await Token.deploy("Token3", "TK3", INITIAL_SUPPLY);
      await token3.waitForDeployment();
      await oracle.setPrice(await token3.getAddress(), TOKEN_PRICE, 8);
      await tokenGate.whitelistToken(await token3.getAddress());

      // Treasury should see new token
      tokens = await treasury.getTreasuryTokens();
      expect(tokens.length).to.equal(3);

      // Should be able to fund with new token
      await token3.approve(await treasury.getAddress(), ethers.parseEther("100"));
      await expect(treasury.fundTreasury(await token3.getAddress(), ethers.parseEther("100")))
        .to.emit(treasury, "TreasuryFunded");
    });

    it("Should reject delisted tokens", async function () {
      // Delist token1 from TokenGate
      await tokenGate.delistToken(await token1.getAddress());

      // Should reject funding
      await token1.approve(await treasury.getAddress(), ethers.parseEther("100"));
      await expect(
        treasury.fundTreasury(await token1.getAddress(), ethers.parseEther("100"))
      ).to.be.revertedWith("Token not whitelisted");
    });
  });
});
