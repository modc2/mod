const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocTimeRegistry Tests', function () {
  let owner, user1, user2;
  let registry;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();

    const BlocTimeRegistry = await ethers.getContractFactory('BlocTimeRegistry');
    registry = await BlocTimeRegistry.deploy();
    await registry.waitForDeployment();
  });

  describe('Module Registration', function () {
    it('should register a module successfully', async function () {
      const pricePerBlock = ethers.parseEther('0.01');
      const maxUsers = 10;
      const ipfsHash = 'QmTest123';

      await expect(registry.connect(user1).registerModule(pricePerBlock, maxUsers, ipfsHash))
        .to.emit(registry, 'ModuleRegistered')
        .withArgs(1, user1.address, pricePerBlock);

      const [owner, price, maxU, currentU, active, hash] = await registry.getModule(1);
      expect(owner).to.equal(user1.address);
      expect(price).to.equal(pricePerBlock);
      expect(maxU).to.equal(maxUsers);
      expect(currentU).to.equal(0);
      expect(active).to.be.true;
      expect(hash).to.equal(ipfsHash);
    });

    it('should reject invalid registration parameters', async function () {
      await expect(
        registry.connect(user1).registerModule(0, 10, 'QmTest')
      ).to.be.revertedWith('Invalid price');

      await expect(
        registry.connect(user1).registerModule(ethers.parseEther('0.01'), 0, 'QmTest')
      ).to.be.revertedWith('Invalid max users');

      await expect(
        registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, '')
      ).to.be.revertedWith('Invalid IPFS hash');
    });

    it('should track user modules', async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest1');
      await registry.connect(user1).registerModule(ethers.parseEther('0.02'), 20, 'QmTest2');

      const userModules = await registry.getUserModules(user1.address);
      expect(userModules.length).to.equal(2);
      expect(userModules[0]).to.equal(1);
      expect(userModules[1]).to.equal(2);
    });
  });

  describe('Module Updates', function () {
    beforeEach(async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
    });

    it('should update module parameters', async function () {
      const newPrice = ethers.parseEther('0.02');
      const newMaxUsers = 20;

      await expect(registry.connect(user1).updateModule(1, newPrice, newMaxUsers))
        .to.emit(registry, 'ModuleUpdated')
        .withArgs(1, newPrice, newMaxUsers);

      const [, price, maxUsers, , ,] = await registry.getModule(1);
      expect(price).to.equal(newPrice);
      expect(maxUsers).to.equal(newMaxUsers);
    });

    it('should reject updates from non-owner', async function () {
      await expect(
        registry.connect(user2).updateModule(1, ethers.parseEther('0.02'), 20)
      ).to.be.revertedWith('Not module owner');
    });

    it('should reject max users below current users', async function () {
      await registry.incrementUsers(1);
      await registry.incrementUsers(1);

      await expect(
        registry.connect(user1).updateModule(1, ethers.parseEther('0.02'), 1)
      ).to.be.revertedWith('Max users below current');
    });
  });

  describe('Module Deactivation', function () {
    beforeEach(async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
    });

    it('should deactivate module', async function () {
      await expect(registry.connect(user1).deactivateModule(1))
        .to.emit(registry, 'ModuleDeactivated')
        .withArgs(1);

      const [, , , , active,] = await registry.getModule(1);
      expect(active).to.be.false;
    });

    it('should reject deactivation from non-owner', async function () {
      await expect(
        registry.connect(user2).deactivateModule(1)
      ).to.be.revertedWith('Not module owner');
    });
  });

  describe('User Count Management', function () {
    beforeEach(async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
    });

    it('should increment user count', async function () {
      await expect(registry.incrementUsers(1))
        .to.emit(registry, 'UserCountChanged')
        .withArgs(1, 1);

      const [, , , currentUsers, ,] = await registry.getModule(1);
      expect(currentUsers).to.equal(1);
    });

    it('should decrement user count', async function () {
      await registry.incrementUsers(1);
      
      await expect(registry.decrementUsers(1))
        .to.emit(registry, 'UserCountChanged')
        .withArgs(1, 0);

      const [, , , currentUsers, ,] = await registry.getModule(1);
      expect(currentUsers).to.equal(0);
    });

    it('should reject increment when max users reached', async function () {
      for (let i = 0; i < 10; i++) {
        await registry.incrementUsers(1);
      }

      await expect(registry.incrementUsers(1)).to.be.revertedWith('Max users reached');
    });

    it('should reject decrement when no users', async function () {
      await expect(registry.decrementUsers(1)).to.be.revertedWith('No users to decrement');
    });
  });

  describe('Module Availability', function () {
    it('should return true for available module', async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
      expect(await registry.isModuleAvailable(1)).to.be.true;
    });

    it('should return false when module is full', async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 2, 'QmTest');
      await registry.incrementUsers(1);
      await registry.incrementUsers(1);

      expect(await registry.isModuleAvailable(1)).to.be.false;
    });

    it('should return false when module is inactive', async function () {
      await registry.connect(user1).registerModule(ethers.parseEther('0.01'), 10, 'QmTest');
      await registry.connect(user1).deactivateModule(1);

      expect(await registry.isModuleAvailable(1)).to.be.false;
    });
  });
});
