const { expect } = require('chai');
const { ethers } = require('hardhat');
const { loadFixture } = require('@nomicfoundation/hardhat-network-helpers');

describe('Safe', function () {
  async function deploySafeFixture() {
    const [owner1, owner2, owner3, user1, user2] = await ethers.getSigners();

    // Deploy Safe singleton
    const Safe = await ethers.getContractFactory('Safe');
    const safeSingleton = await Safe.deploy();
    await safeSingleton.waitForDeployment();
    const singletonAddress = await safeSingleton.getAddress();

    // Deploy SafeProxyFactory
    const SafeProxyFactory = await ethers.getContractFactory('SafeProxyFactory');
    const proxyFactory = await SafeProxyFactory.deploy();
    await proxyFactory.waitForDeployment();

    return {
      safeSingleton,
      singletonAddress,
      proxyFactory,
      owner1,
      owner2,
      owner3,
      user1,
      user2,
    };
  }

  async function createSafe(proxyFactory, singletonAddress, owners, threshold) {
    const Safe = await ethers.getContractFactory('Safe');

    const setupData = Safe.interface.encodeFunctionData('setup', [
      owners,
      threshold,
      ethers.ZeroAddress,
      '0x',
      ethers.ZeroAddress,
      ethers.ZeroAddress,
      0,
      ethers.ZeroAddress,
    ]);

    const saltNonce = Date.now() + Math.floor(Math.random() * 1000000);
    const tx = await proxyFactory.createProxyWithNonce(singletonAddress, setupData, saltNonce);
    const receipt = await tx.wait();

    // Get proxy address from event
    const event = receipt.logs.find(
      (log) => log.topics[0] === proxyFactory.interface.getEvent('ProxyCreation').topicHash
    );
    const proxyAddress = '0x' + event.topics[1].slice(26);

    return await ethers.getContractAt('Safe', proxyAddress);
  }

  describe('Deployment', function () {
    it('Should deploy Safe singleton', async function () {
      const { safeSingleton, singletonAddress } = await loadFixture(deploySafeFixture);
      expect(singletonAddress).to.properAddress;
      expect(await safeSingleton.VERSION()).to.equal('1.0.0');
    });

    it('Should deploy SafeProxyFactory', async function () {
      const { proxyFactory } = await loadFixture(deploySafeFixture);
      expect(await proxyFactory.getAddress()).to.properAddress;
    });
  });

  describe('Safe Creation', function () {
    it('Should create a Safe with single owner', async function () {
      const { proxyFactory, singletonAddress, owner1 } = await loadFixture(deploySafeFixture);

      const safe = await createSafe(proxyFactory, singletonAddress, [owner1.address], 1);

      const owners = await safe.getOwners();
      expect(owners).to.deep.equal([owner1.address]);
      expect(await safe.getThreshold()).to.equal(1);
      expect(await safe.isOwner(owner1.address)).to.be.true;
    });

    it('Should create a Safe with multiple owners', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2, owner3 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(
        proxyFactory,
        singletonAddress,
        [owner1.address, owner2.address, owner3.address],
        2
      );

      const owners = await safe.getOwners();
      expect(owners.length).to.equal(3);
      expect(owners).to.include(owner1.address);
      expect(owners).to.include(owner2.address);
      expect(owners).to.include(owner3.address);
      expect(await safe.getThreshold()).to.equal(2);
    });

    it('Should reject initialization with zero threshold', async function () {
      const { proxyFactory, singletonAddress, owner1 } = await loadFixture(deploySafeFixture);
      const Safe = await ethers.getContractFactory('Safe');

      const setupData = Safe.interface.encodeFunctionData('setup', [
        [owner1.address],
        0, // Invalid threshold
        ethers.ZeroAddress,
        '0x',
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        0,
        ethers.ZeroAddress,
      ]);

      await expect(
        proxyFactory.createProxyWithNonce(singletonAddress, setupData, Date.now())
      ).to.be.reverted;
    });

    it('Should reject initialization with threshold > owner count', async function () {
      const { proxyFactory, singletonAddress, owner1 } = await loadFixture(deploySafeFixture);
      const Safe = await ethers.getContractFactory('Safe');

      const setupData = Safe.interface.encodeFunctionData('setup', [
        [owner1.address],
        2, // Threshold exceeds owner count
        ethers.ZeroAddress,
        '0x',
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        0,
        ethers.ZeroAddress,
      ]);

      await expect(
        proxyFactory.createProxyWithNonce(singletonAddress, setupData, Date.now())
      ).to.be.reverted;
    });

    it('Should reject duplicate owners', async function () {
      const { proxyFactory, singletonAddress, owner1 } = await loadFixture(deploySafeFixture);
      const Safe = await ethers.getContractFactory('Safe');

      const setupData = Safe.interface.encodeFunctionData('setup', [
        [owner1.address, owner1.address], // Duplicate
        1,
        ethers.ZeroAddress,
        '0x',
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        0,
        ethers.ZeroAddress,
      ]);

      await expect(
        proxyFactory.createProxyWithNonce(singletonAddress, setupData, Date.now())
      ).to.be.reverted;
    });
  });

  describe('Transaction Execution', function () {
    it('Should execute transaction with single owner', async function () {
      const { proxyFactory, singletonAddress, owner1, user1 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(proxyFactory, singletonAddress, [owner1.address], 1);

      // Fund the safe
      await owner1.sendTransaction({
        to: await safe.getAddress(),
        value: ethers.parseEther('1.0'),
      });

      // Prepare transaction
      const to = user1.address;
      const value = ethers.parseEther('0.5');
      const data = '0x';
      const operation = 0; // Call
      const safeTxGas = 0;
      const baseGas = 0;
      const gasPrice = 0;
      const gasToken = ethers.ZeroAddress;
      const refundReceiver = ethers.ZeroAddress;
      const nonce = await safe.nonce();

      // Get transaction hash
      const txHash = await safe.getTransactionHash(
        to,
        value,
        data,
        operation,
        safeTxGas,
        baseGas,
        gasPrice,
        gasToken,
        refundReceiver,
        nonce
      );

      // Sign with owner1
      const signature = await owner1.signMessage(ethers.getBytes(txHash));

      // Execute transaction
      const userBalanceBefore = await ethers.provider.getBalance(user1.address);

      await expect(
        safe.execTransaction(
          to,
          value,
          data,
          operation,
          safeTxGas,
          baseGas,
          gasPrice,
          gasToken,
          refundReceiver,
          signature
        )
      ).to.emit(safe, 'ExecutionSuccess');

      const userBalanceAfter = await ethers.provider.getBalance(user1.address);
      expect(userBalanceAfter - userBalanceBefore).to.equal(value);
    });

    it('Should execute transaction with multiple owners meeting threshold', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2, owner3, user1 } =
        await loadFixture(deploySafeFixture);

      const safe = await createSafe(
        proxyFactory,
        singletonAddress,
        [owner1.address, owner2.address, owner3.address],
        2 // Require 2 signatures
      );

      // Fund the safe
      await owner1.sendTransaction({
        to: await safe.getAddress(),
        value: ethers.parseEther('1.0'),
      });

      // Prepare transaction
      const to = user1.address;
      const value = ethers.parseEther('0.5');
      const data = '0x';
      const operation = 0;
      const safeTxGas = 0;
      const baseGas = 0;
      const gasPrice = 0;
      const gasToken = ethers.ZeroAddress;
      const refundReceiver = ethers.ZeroAddress;
      const nonce = await safe.nonce();

      // Get transaction hash
      const txHash = await safe.getTransactionHash(
        to,
        value,
        data,
        operation,
        safeTxGas,
        baseGas,
        gasPrice,
        gasToken,
        refundReceiver,
        nonce
      );

      // Sign with owner1 and owner2 (need 2 signatures)
      const sig1 = await owner1.signMessage(ethers.getBytes(txHash));
      const sig2 = await owner2.signMessage(ethers.getBytes(txHash));

      // Combine signatures in ascending order of signer addresses
      const signers = [
        { address: owner1.address, signature: sig1 },
        { address: owner2.address, signature: sig2 },
      ].sort((a, b) => (a.address.toLowerCase() < b.address.toLowerCase() ? -1 : 1));

      const combinedSignatures = signers[0].signature + signers[1].signature.slice(2);

      // Execute transaction
      const userBalanceBefore = await ethers.provider.getBalance(user1.address);

      await expect(
        safe.execTransaction(
          to,
          value,
          data,
          operation,
          safeTxGas,
          baseGas,
          gasPrice,
          gasToken,
          refundReceiver,
          combinedSignatures
        )
      ).to.emit(safe, 'ExecutionSuccess');

      const userBalanceAfter = await ethers.provider.getBalance(user1.address);
      expect(userBalanceAfter - userBalanceBefore).to.equal(value);
    });

    it('Should reject transaction with insufficient signatures', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2, user1 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(
        proxyFactory,
        singletonAddress,
        [owner1.address, owner2.address],
        2 // Require 2 signatures
      );

      // Fund the safe
      await owner1.sendTransaction({
        to: await safe.getAddress(),
        value: ethers.parseEther('1.0'),
      });

      const to = user1.address;
      const value = ethers.parseEther('0.5');
      const data = '0x';
      const operation = 0;
      const nonce = await safe.nonce();

      const txHash = await safe.getTransactionHash(
        to,
        value,
        data,
        operation,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        nonce
      );

      // Only sign with owner1 (need 2)
      const signature = await owner1.signMessage(ethers.getBytes(txHash));

      await expect(
        safe.execTransaction(
          to,
          value,
          data,
          operation,
          0,
          0,
          0,
          ethers.ZeroAddress,
          ethers.ZeroAddress,
          signature
        )
      ).to.be.revertedWith('Safe: Not enough signatures');
    });

    it('Should increment nonce after execution', async function () {
      const { proxyFactory, singletonAddress, owner1, user1 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(proxyFactory, singletonAddress, [owner1.address], 1);

      await owner1.sendTransaction({
        to: await safe.getAddress(),
        value: ethers.parseEther('1.0'),
      });

      const nonceBefore = await safe.nonce();

      const to = user1.address;
      const value = ethers.parseEther('0.1');
      const txHash = await safe.getTransactionHash(
        to,
        value,
        '0x',
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        nonceBefore
      );

      const signature = await owner1.signMessage(ethers.getBytes(txHash));

      await safe.execTransaction(
        to,
        value,
        '0x',
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        signature
      );

      const nonceAfter = await safe.nonce();
      expect(nonceAfter).to.equal(nonceBefore + BigInt(1));
    });
  });

  describe('Owner Management', function () {
    it('Should add owner with threshold via execTransaction', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(proxyFactory, singletonAddress, [owner1.address], 1);

      // Prepare addOwnerWithThreshold call
      const addOwnerData = safe.interface.encodeFunctionData('addOwnerWithThreshold', [
        owner2.address,
        2,
      ]);

      const to = await safe.getAddress();
      const nonce = await safe.nonce();

      const txHash = await safe.getTransactionHash(
        to,
        0,
        addOwnerData,
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        nonce
      );

      const signature = await owner1.signMessage(ethers.getBytes(txHash));

      await safe.execTransaction(
        to,
        0,
        addOwnerData,
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        signature
      );

      const owners = await safe.getOwners();
      expect(owners.length).to.equal(2);
      expect(await safe.isOwner(owner2.address)).to.be.true;
      expect(await safe.getThreshold()).to.equal(2);
    });

    it('Should remove owner via execTransaction', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(
        proxyFactory,
        singletonAddress,
        [owner1.address, owner2.address],
        1
      );

      const owners = await safe.getOwners();
      const prevOwner = owners[0] === owner2.address ? owners[0] : owners[1];
      const ownerToRemove = owners[0] === owner2.address ? owners[1] : owners[0];

      // Get the actual previous owner for owner2
      let actualPrevOwner;
      if (owners[0] === owner2.address) {
        actualPrevOwner = '0x0000000000000000000000000000000000000001'; // SENTINEL
      } else {
        actualPrevOwner = owners[0];
      }

      const removeOwnerData = safe.interface.encodeFunctionData('removeOwner', [
        actualPrevOwner,
        owner2.address,
        1,
      ]);

      const to = await safe.getAddress();
      const nonce = await safe.nonce();

      const txHash = await safe.getTransactionHash(
        to,
        0,
        removeOwnerData,
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        nonce
      );

      const signature = await owner1.signMessage(ethers.getBytes(txHash));

      await safe.execTransaction(
        to,
        0,
        removeOwnerData,
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        signature
      );

      const newOwners = await safe.getOwners();
      expect(newOwners.length).to.equal(1);
      expect(await safe.isOwner(owner2.address)).to.be.false;
    });

    it('Should change threshold via execTransaction', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2, owner3 } = await loadFixture(
        deploySafeFixture
      );

      const safe = await createSafe(
        proxyFactory,
        singletonAddress,
        [owner1.address, owner2.address, owner3.address],
        1
      );

      const changeThresholdData = safe.interface.encodeFunctionData('changeThreshold', [3]);

      const to = await safe.getAddress();
      const nonce = await safe.nonce();

      const txHash = await safe.getTransactionHash(
        to,
        0,
        changeThresholdData,
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        nonce
      );

      const signature = await owner1.signMessage(ethers.getBytes(txHash));

      await safe.execTransaction(
        to,
        0,
        changeThresholdData,
        0,
        0,
        0,
        0,
        ethers.ZeroAddress,
        ethers.ZeroAddress,
        signature
      );

      expect(await safe.getThreshold()).to.equal(3);
    });
  });

  describe('Receive ETH', function () {
    it('Should receive ETH', async function () {
      const { proxyFactory, singletonAddress, owner1 } = await loadFixture(deploySafeFixture);

      const safe = await createSafe(proxyFactory, singletonAddress, [owner1.address], 1);
      const safeAddress = await safe.getAddress();

      const amount = ethers.parseEther('1.0');
      await owner1.sendTransaction({ to: safeAddress, value: amount });

      expect(await ethers.provider.getBalance(safeAddress)).to.equal(amount);
    });
  });
});
