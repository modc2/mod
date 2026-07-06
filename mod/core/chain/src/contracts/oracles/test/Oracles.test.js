const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Oracle Adapters", function () {
  let owner, user1;

  beforeEach(async function () {
    [owner, user1] = await ethers.getSigners();
  });

  describe("ManualPriceOracle", function () {
    let oracle, token;
    const price = 100000000n; // $1 with 8 decimals
    const decimals = 8;

    beforeEach(async function () {
      const Token = await ethers.getContractFactory("Token");
      token = await Token.deploy("Test", "TST", ethers.parseEther("1000000"));
      await token.waitForDeployment();

      const ManualPriceOracle = await ethers.getContractFactory("ManualPriceOracle");
      oracle = await ManualPriceOracle.deploy();
      await oracle.waitForDeployment();
    });

    it("Should set price correctly", async function () {
      await expect(oracle.setPrice(await token.getAddress(), price, decimals))
        .to.emit(oracle, "PriceUpdated");

      const [returnedPrice, returnedDecimals] = await oracle.getPrice(await token.getAddress());
      expect(returnedPrice).to.equal(price);
      expect(returnedDecimals).to.equal(decimals);
    });

    it("Should batch set prices", async function () {
      const Token = await ethers.getContractFactory("Token");
      const token2 = await Token.deploy("Test2", "TST2", ethers.parseEther("1000000"));
      await token2.waitForDeployment();

      await oracle.batchSetPrices(
        [await token.getAddress(), await token2.getAddress()],
        [price, price * 2n],
        [decimals, decimals]
      );

      expect(await oracle.hasPriceFeed(await token.getAddress())).to.be.true;
      expect(await oracle.hasPriceFeed(await token2.getAddress())).to.be.true;
    });

    it("Should remove price", async function () {
      await oracle.setPrice(await token.getAddress(), price, decimals);

      await expect(oracle.removePrice(await token.getAddress()))
        .to.emit(oracle, "PriceRemoved");

      expect(await oracle.hasPriceFeed(await token.getAddress())).to.be.false;
    });

    it("Should reject invalid price", async function () {
      await expect(
        oracle.setPrice(await token.getAddress(), 0, decimals)
      ).to.be.revertedWith("Invalid price");
    });

    it("Should reject non-owner", async function () {
      await expect(
        oracle.connect(user1).setPrice(await token.getAddress(), price, decimals)
      ).to.be.reverted;
    });
  });

  describe("ChainlinkAdapter", function () {
    let adapter, token;

    beforeEach(async function () {
      const Token = await ethers.getContractFactory("Token");
      token = await Token.deploy("Test", "TST", ethers.parseEther("1000000"));
      await token.waitForDeployment();

      const ChainlinkAdapter = await ethers.getContractFactory("ChainlinkAdapter");
      adapter = await ChainlinkAdapter.deploy();
      await adapter.waitForDeployment();
    });

    it("Should set price feed", async function () {
      const mockFeed = ethers.Wallet.createRandom().address;

      await expect(adapter.setPriceFeed(await token.getAddress(), mockFeed))
        .to.emit(adapter, "PriceFeedSet");

      expect(await adapter.priceFeeds(await token.getAddress())).to.equal(mockFeed);
    });

    it("Should remove price feed", async function () {
      const mockFeed = ethers.Wallet.createRandom().address;
      await adapter.setPriceFeed(await token.getAddress(), mockFeed);

      await expect(adapter.removePriceFeed(await token.getAddress()))
        .to.emit(adapter, "PriceFeedRemoved");

      expect(await adapter.hasPriceFeed(await token.getAddress())).to.be.false;
    });
  });

  describe("PythAdapter", function () {
    let adapter, token;
    const mockPythAddress = ethers.Wallet.createRandom().address;

    beforeEach(async function () {
      const Token = await ethers.getContractFactory("Token");
      token = await Token.deploy("Test", "TST", ethers.parseEther("1000000"));
      await token.waitForDeployment();

      const PythAdapter = await ethers.getContractFactory("PythAdapter");
      adapter = await PythAdapter.deploy(mockPythAddress);
      await adapter.waitForDeployment();
    });

    it("Should set price ID", async function () {
      const priceId = ethers.randomBytes(32);

      await expect(adapter.setPriceId(await token.getAddress(), priceId))
        .to.emit(adapter, "PriceIdSet");

      expect(await adapter.priceIds(await token.getAddress())).to.equal(ethers.hexlify(priceId));
    });

    it("Should remove price ID", async function () {
      const priceId = ethers.randomBytes(32);
      await adapter.setPriceId(await token.getAddress(), priceId);

      await expect(adapter.removePriceId(await token.getAddress()))
        .to.emit(adapter, "PriceIdRemoved");

      expect(await adapter.hasPriceFeed(await token.getAddress())).to.be.false;
    });
  });
});
