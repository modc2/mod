const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocTimeMarketplaceV3 Tests', function () {
  let owner, user1, user2, user3;
  let nativeToken, marketplace, registry, staking;
  const INITIAL_SUPPLY = ethers.parseEther('1000000');
  const TREASURY_FEE_BPS = 250;

  beforeEach(async function () {
    [owner, user1, user2, user3] = await ethers.getSigners();

    const MockERC20 = await ethers.getContractFactory('MockERC20');
    nativeToken = await MockERC20.deploy('Native Token', 'NAT', INITIAL_SUPPLY);
    await nativeToken.waitForDeployment();

    const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
    staking = await BlocTimeStaking.deploy(
      await nativeToken.getAddress(),
      'BlocTime Token',
      'BLOC',
      100000,
      5000
    );
    await staking.waitForDeployment();

    const BlocTimeRegistry = await ethers.getContractFactory('BlocTimeRegistry');
    registry = await BlocTimeRegistry.deploy();
    await registry.waitForDeployment();

    const BlocTimeMarketplaceV3 = await ethers.getContractFactory('BlocTimeMarketplaceV3');
    marketplace = await BlocTimeMarketplaceV3.deploy(
      await nativeToken.getAddress(),
      await staking.getAddress(),
      await registry.getAddress(),
      TREASURY_FEE_BPS
    );
    await marketplace.waitForDeployment();

    await nativeToken.transfer(user1.address, ethers.parseEther('10000'));
    await nativeToken.transfer(user2.address, ethers.parseEther('10000'));
    await nativeToken.transfer(user3.address, ethers.parseEther('10000'));
  });

  describe('Rental Flow', function () {
    it('should rent a module successfully', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 1000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      
      await expect(marketplace.connect(user2).rent(1, blocks))
        .to.emit(marketplace, 'Rented')
        .withArgs(1, 1, user2.address, blocks, cost);

      const [renter, moduleId, startBlock, paidBlocks, active] = await marketplace.getRental(1);
      expect(renter).to.equal(user2.address);
      expect(moduleId).to.equal(1);
      expect(paidBlocks).to.equal(blocks);
      expect(active).to.be.true;
    });

    it('should calculate treasury fee correctly', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 1000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, blocks);

      const treasuryBalance = await staking.treasuryBalance();
      const expectedFee = (cost * BigInt(TREASURY_FEE_BPS)) / 10000n;
      expect(treasuryBalance).to.equal(expectedFee);
    });
  });

  describe('Fractional Listings', function () {
    it('should list fractional rental for sale', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 10000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, blocks);

      const listingPrice = ethers.parseEther('5');
      await expect(marketplace.connect(user2).listFractionalForSale(1, 5000, 8000, listingPrice))
        .to.emit(marketplace, 'ListedFractional')
        .withArgs(1, 1, 5000, 8000, listingPrice);

      const [seller, rentalId, fromBlock, toBlock, price, active] = await marketplace.getListing(1);
      expect(seller).to.equal(user2.address);
      expect(rentalId).to.equal(1);
      expect(fromBlock).to.equal(5000);
      expect(toBlock).to.equal(8000);
      expect(price).to.equal(listingPrice);
      expect(active).to.be.true;
    });

    it('should prevent overlapping listings', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 10000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, blocks);

      await marketplace.connect(user2).listFractionalForSale(1, 5000, 8000, ethers.parseEther('5'));
      
      await expect(
        marketplace.connect(user2).listFractionalForSale(1, 6000, 9000, ethers.parseEther('5'))
      ).to.be.revertedWith('Overlapping listing exists');
    });

    it('should buy fractional listing', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 10000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, blocks);

      const listingPrice = ethers.parseEther('5');
      await marketplace.connect(user2).listFractionalForSale(1, 5000, 8000, listingPrice);

      await nativeToken.connect(user3).approve(await marketplace.getAddress(), listingPrice);
      await expect(marketplace.connect(user3).buy(1))
        .to.emit(marketplace, 'Sold')
        .withArgs(1, user3.address, listingPrice);

      const [, , , , active] = await marketplace.getListing(1);
      expect(active).to.be.false;

      const userRentals = await marketplace.getUserRentals(user3.address);
      expect(userRentals.length).to.equal(1);
    });
  });

  describe('Rental Management', function () {
    it('should end rental and deactivate listings', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 10000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, blocks);

      await marketplace.connect(user2).listFractionalForSale(1, 5000, 8000, ethers.parseEther('5'));

      await expect(marketplace.connect(user2).endRental(1))
        .to.emit(marketplace, 'RentalEnded')
        .withArgs(1);

      const [, , , , rentalActive] = await marketplace.getRental(1);
      expect(rentalActive).to.be.false;

      const [, , , , listingActive] = await marketplace.getListing(1);
      expect(listingActive).to.be.false;
    });

    it('should calculate remaining blocks correctly', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest123');

      const blocks = 1000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, blocks);

      const remaining = await marketplace.getRemainingBlocks(1);
      expect(remaining).to.be.lte(blocks);
    });
  });
});
