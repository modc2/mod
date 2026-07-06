const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("TokenGate", function () {
  let tokenGate, oracle, token, owner, user1;
  const price = 100000000n; // $1 with 8 decimals
  const decimals = 8;

  beforeEach(async function () {
    [owner, user1] = await ethers.getSigners();

    const Token = await ethers.getContractFactory("Token");
    token = await Token.deploy("Test", "TST", ethers.parseEther("1000000"));
    await token.waitForDeployment();

    const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
    oracle = await ManualPriceOracle.deploy();
    await oracle.waitForDeployment();

    const TokenGate = await ethers.getContractFactory("TokenGate");
    tokenGate = await TokenGate.deploy(await oracle.getAddress());
    await tokenGate.waitForDeployment();
  });

  describe("Deployment", function () {
    it("Should set default oracle correctly", async function () {
      expect(await tokenGate.defaultOracle()).to.equal(await oracle.getAddress());
    });

    it("Should reject zero address oracle", async function () {
      const TokenGate = await ethers.getContractFactory("TokenGate");
      await expect(TokenGate.deploy(ethers.ZeroAddress)).to.be.revertedWith("Invalid oracle");
    });
  });

  describe("Oracle Management", function () {
    it("Should update default oracle", async function () {
      const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
      const newOracle = await ManualPriceOracle.deploy();
      await newOracle.waitForDeployment();

      await expect(tokenGate.setDefaultOracle(await newOracle.getAddress()))
        .to.emit(tokenGate, "DefaultOracleUpdated");

      expect(await tokenGate.defaultOracle()).to.equal(await newOracle.getAddress());
    });

    it("Should register token-specific oracle", async function () {
      const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
      const tokenOracle = await ManualPriceOracle.deploy();
      await tokenOracle.waitForDeployment();

      await expect(tokenGate.registerTokenOracle(await token.getAddress(), await tokenOracle.getAddress()))
        .to.emit(tokenGate, "TokenOracleRegistered");

      const oracleForToken = await tokenGate.getOracleForToken(await token.getAddress());
      expect(oracleForToken).to.equal(await tokenOracle.getAddress());
    });

    it("Should remove token-specific oracle", async function () {
      const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
      const tokenOracle = await ManualPriceOracle.deploy();
      await tokenOracle.waitForDeployment();

      await tokenGate.registerTokenOracle(await token.getAddress(), await tokenOracle.getAddress());
      
      await expect(tokenGate.removeTokenOracle(await token.getAddress()))
        .to.emit(tokenGate, "TokenOracleRemoved");

      const oracleForToken = await tokenGate.getOracleForToken(await token.getAddress());
      expect(oracleForToken).to.equal(await oracle.getAddress());
    });
  });

  describe("Token Whitelist", function () {
    beforeEach(async function () {
      await oracle.setPrice(await token.getAddress(), price, decimals);
    });

    it("Should whitelist token with oracle price feed", async function () {
      await expect(tokenGate.whitelistToken(await token.getAddress()))
        .to.emit(tokenGate, "TokenWhitelisted");

      expect(await tokenGate.isTokenWhitelisted(await token.getAddress())).to.be.true;
    });

    it("Should reject whitelisting without oracle price feed", async function () {
      const Token = await ethers.getContractFactory("Token");
      const badToken = await Token.deploy("Bad", "BAD", ethers.parseEther("1000000"));
      await badToken.waitForDeployment();

      await expect(
        tokenGate.whitelistToken(await badToken.getAddress())
      ).to.be.revertedWith("No oracle price feed");
    });

    it("Should batch whitelist tokens", async function () {
      const Token = await ethers.getContractFactory("Token");
      const token2 = await Token.deploy("Test2", "TST2", ethers.parseEther("1000000"));
      await token2.waitForDeployment();

      await oracle.setPrice(await token2.getAddress(), price * 2n, decimals);

      await tokenGate.batchWhitelistTokens([
        await token.getAddress(),
        await token2.getAddress()
      ]);

      expect(await tokenGate.isTokenWhitelisted(await token.getAddress())).to.be.true;
      expect(await tokenGate.isTokenWhitelisted(await token2.getAddress())).to.be.true;
    });

    it("Should delist token and cleanup storage", async function () {
      await tokenGate.whitelistToken(await token.getAddress());
      
      await expect(tokenGate.delistToken(await token.getAddress()))
        .to.emit(tokenGate, "TokenDelisted");

      expect(await tokenGate.isTokenWhitelisted(await token.getAddress())).to.be.false;
      
      const tokenList = await tokenGate.getTokenList();
      expect(tokenList.length).to.equal(0);
    });

    it("Should get token price from oracle", async function () {
      await tokenGate.whitelistToken(await token.getAddress());
      
      const [returnedPrice, returnedDecimals] = await tokenGate.getTokenPrice(await token.getAddress());
      expect(returnedPrice).to.equal(price);
      expect(returnedDecimals).to.equal(decimals);
    });
  });

  describe("Token-Specific Oracle Integration", function () {
    it("Should use token-specific oracle over default", async function () {
      const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
      const tokenOracle = await ManualPriceOracle.deploy();
      await tokenOracle.waitForDeployment();

      const tokenSpecificPrice = 200000000n; // $2 with 8 decimals
      await tokenOracle.setPrice(await token.getAddress(), tokenSpecificPrice, decimals);
      await oracle.setPrice(await token.getAddress(), price, decimals);

      await tokenGate.registerTokenOracle(await token.getAddress(), await tokenOracle.getAddress());
      await tokenGate.whitelistToken(await token.getAddress());

      const [returnedPrice] = await tokenGate.getTokenPrice(await token.getAddress());
      expect(returnedPrice).to.equal(tokenSpecificPrice);
    });

    it("Should fall back to default oracle when token oracle removed", async function () {
      const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
      const tokenOracle = await ManualPriceOracle.deploy();
      await tokenOracle.waitForDeployment();

      await tokenOracle.setPrice(await token.getAddress(), 200000000n, decimals);
      await oracle.setPrice(await token.getAddress(), price, decimals);

      await tokenGate.registerTokenOracle(await token.getAddress(), await tokenOracle.getAddress());
      await tokenGate.whitelistToken(await token.getAddress());
      await tokenGate.removeTokenOracle(await token.getAddress());

      const [returnedPrice] = await tokenGate.getTokenPrice(await token.getAddress());
      expect(returnedPrice).to.equal(price);
    });
  });
});
