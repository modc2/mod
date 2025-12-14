const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocTime Integration Tests', function () {
  let owner, user1, user2, user3;
  let nativeToken, blocTimeToken;
  let staking, registry, marketplace, integration;
  
  const INITIAL_SUPPLY = ethers.parseEther('1000000');
  const STAKE_AMOUNT = ethers.parseEther('1000');
  const MAX_LOCK_BLOCKS = 100000;
  const TREASURY_FEE_BPS = 250; // 2.5%
  const DISTRIBUTION_PCT = 5000; // 50%

  beforeEach(async function () {
    [owner, user1, user2, user3] = await ethers.getSigners();

    // Deploy mock ERC20 token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    nativeToken = await MockERC20.deploy('Native Token', 'NAT', INITIAL_SUPPLY);
    await nativeToken.waitForDeployment();

    // Deploy Staking
    const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
    staking = await BlocTimeStaking.deploy(
      await nativeToken.getAddress(),
      'BlocTime Token',
      'BLOC',
      MAX_LOCK_BLOCKS,
      DISTRIBUTION_PCT
    );
    await staking.waitForDeployment();

    blocTimeToken = await ethers.getContractAt('BlocTimeToken', await staking.blocTimeToken());

    // Set multiplier points
    const points = [
      { blocks: 0, multiplier: 10000 },
      { blocks: 10000, multiplier: 15000 },
      { blocks: 50000, multiplier: 20000 },
      { blocks: 100000, multiplier: 30000 }
    ];
    await staking.setPoints(points);

    // Deploy Registry
    const BlocTimeRegistry = await ethers.getContractFactory('BlocTimeRegistry');
    registry = await BlocTimeRegistry.deploy();
    await registry.waitForDeployment();

    // Deploy Marketplace
    const BlocTimeMarketplaceV3 = await ethers.getContractFactory('BlocTimeMarketplaceV3');
    marketplace = await BlocTimeMarketplaceV3.deploy(
      await nativeToken.getAddress(),
      await staking.getAddress(),
      await registry.getAddress(),
      TREASURY_FEE_BPS
    );
    await marketplace.waitForDeployment();

    // Deploy Integration
    const BlocTimeIntegration = await ethers.getContractFactory('BlocTimeIntegration');
    integration = await BlocTimeIntegration.deploy(
      await marketplace.getAddress(),
      await registry.getAddress(),
      await staking.getAddress()
    );
    await integration.waitForDeployment();

    // Distribute tokens
    await nativeToken.transfer(user1.address, ethers.parseEther('10000'));
    await nativeToken.transfer(user2.address, ethers.parseEther('10000'));
    await nativeToken.transfer(user3.address, ethers.parseEther('10000'));
  });

  describe('System Health', function () {
    it('should pass health check', async function () {
      const [marketplaceHealthy, registryHealthy, stakingHealthy, status] = await integration.healthCheck();
      expect(marketplaceHealthy).to.be.true;
      expect(registryHealthy).to.be.true;
      expect(stakingHealthy).to.be.true;
      expect(status).to.equal('All systems operational');
    });
  });

  describe('End-to-End Flow', function () {
    it('should complete full staking and marketplace cycle', async function () {
      // 1. User1 stakes tokens
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 50000);
      
      const blocTimeBalance = await blocTimeToken.balanceOf(user1.address);
      expect(blocTimeBalance).to.equal(STAKE_AMOUNT * 2n); // 2x multiplier at 50k blocks

      // 2. User2 registers a module
      const pricePerBlock = ethers.parseEther('0.01');
      const maxUsers = 10;
      const ipfsHash = 'QmTest123';
      await registry.connect(user2).registerModule(pricePerBlock, maxUsers, ipfsHash);
      const moduleId = 1;

      // 3. User3 rents the module
      const blocks = 1000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user3).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user3).rent(moduleId, blocks);

      // 4. Check treasury was funded
      const treasuryBalance = await staking.treasuryBalance();
      const expectedFee = (cost * BigInt(TREASURY_FEE_BPS)) / 10000n;
      expect(treasuryBalance).to.equal(expectedFee);

      // 5. User1 claims rewards
      const pendingRewards = await staking.pendingRewards(user1.address);
      expect(pendingRewards).to.be.gt(0);
      
      await staking.connect(user1).claimRewards();
      const finalBalance = await nativeToken.balanceOf(user1.address);
      expect(finalBalance).to.be.gt(ethers.parseEther('9000'));
    });

    it('should handle secondary market with treasury funding', async function () {
      // Setup: Register module and create rental
      const pricePerBlock = ethers.parseEther('0.01');
      await registry.connect(user1).registerModule(pricePerBlock, 10, 'QmTest');
      
      const blocks = 10000;
      const cost = pricePerBlock * BigInt(blocks);
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      const rentalId = await marketplace.connect(user2).rent.staticCall(1, blocks);
      await marketplace.connect(user2).rent(1, blocks);

      // List fractional rental
      const listingPrice = ethers.parseEther('5');
      await marketplace.connect(user2).listFractionalForSale(rentalId, 5000, 8000, listingPrice);
      const listingId = 1;

      // User3 buys from secondary market
      const initialTreasury = await staking.treasuryBalance();
      await nativeToken.connect(user3).approve(await marketplace.getAddress(), listingPrice);
      await marketplace.connect(user3).buy(listingId);

      // Verify treasury received fee from secondary sale
      const finalTreasury = await staking.treasuryBalance();
      const expectedFee = (listingPrice * BigInt(TREASURY_FEE_BPS)) / 10000n;
      expect(finalTreasury - initialTreasury).to.equal(expectedFee);
    });
  });

  describe('System Statistics', function () {
    it('should track comprehensive system stats', async function () {
      // Create some activity
      await nativeToken.connect(user1).approve(await staking.getAddress(), STAKE_AMOUNT);
      await staking.connect(user1).stake(STAKE_AMOUNT, 50000);
      
      await registry.connect(user2).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
      
      const cost = ethers.parseEther('10');
      await nativeToken.connect(user3).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user3).rent(1, 1000);

      const [totalModules, totalRentals, totalStaked, totalBlocTime, treasuryBalance] = 
        await integration.getSystemStats();

      expect(totalModules).to.equal(1);
      expect(totalRentals).to.equal(1);
      expect(totalStaked).to.be.gt(0);
      expect(totalBlocTime).to.equal(STAKE_AMOUNT * 2n);
      expect(treasuryBalance).to.be.gt(0);
    });
  });

  describe('Validation Functions', function () {
    it('should validate module registration', async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
      
      const [valid, reason] = await integration.validateModuleRegistration(1);
      expect(valid).to.be.true;
      expect(reason).to.equal('Module valid');
    });

    it('should validate rental flow', async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
      
      const cost = ethers.parseEther('10');
      await nativeToken.connect(user2).approve(await marketplace.getAddress(), cost);
      await marketplace.connect(user2).rent(1, 1000);

      const [valid, reason] = await integration.validateRentalFlow(1);
      expect(valid).to.be.true;
      expect(reason).to.equal('Rental valid');
    });
  });
});

// Mock ERC20 for testing
const MockERC20Code = `
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockERC20 is ERC20 {
    constructor(string memory name, string memory symbol, uint256 initialSupply) ERC20(name, symbol) {
        _mint(msg.sender, initialSupply);
    }
}
`;
