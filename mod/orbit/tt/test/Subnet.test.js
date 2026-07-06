const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Subnet", function () {
  let subnet;
  let owner, user1, minter;

  beforeEach(async function () {
    [owner, user1, minter] = await ethers.getSigners();

    const Subnet = await ethers.getContractFactory("Subnet");
    subnet = await Subnet.deploy("TestNet", "TST", ethers.parseEther("1000000"));
    await subnet.waitForDeployment();
  });

  describe("Deployment", function () {
    it("sets name and symbol", async function () {
      expect(await subnet.name()).to.equal("TestNet");
      expect(await subnet.symbol()).to.equal("TST");
    });

    it("mints initial supply to deployer", async function () {
      expect(await subnet.balanceOf(owner.address)).to.equal(ethers.parseEther("1000000"));
    });

    it("deploys with zero initial supply", async function () {
      const Subnet = await ethers.getContractFactory("Subnet");
      const s = await Subnet.deploy("Empty", "EMP", 0);
      await s.waitForDeployment();
      expect(await s.totalSupply()).to.equal(0);
    });
  });

  describe("Minter role", function () {
    it("owner sets minter", async function () {
      await expect(subnet.setMinter(minter.address))
        .to.emit(subnet, "MinterSet")
        .withArgs(minter.address);
      expect(await subnet.minter()).to.equal(minter.address);
    });

    it("non-owner cannot set minter", async function () {
      await expect(
        subnet.connect(user1).setMinter(minter.address)
      ).to.be.revertedWith("Ownable: caller is not the owner");
    });

    it("minter can mint tokens", async function () {
      await subnet.setMinter(minter.address);
      await subnet.connect(minter).mint(user1.address, ethers.parseEther("500"));
      expect(await subnet.balanceOf(user1.address)).to.equal(ethers.parseEther("500"));
    });

    it("non-minter cannot mint", async function () {
      await expect(
        subnet.connect(user1).mint(user1.address, ethers.parseEther("500"))
      ).to.be.revertedWith("not minter");
    });

    it("owner cannot mint without being minter", async function () {
      await expect(
        subnet.mint(user1.address, ethers.parseEther("500"))
      ).to.be.revertedWith("not minter");
    });
  });

  describe("ERC20", function () {
    it("transfers tokens", async function () {
      await subnet.transfer(user1.address, ethers.parseEther("100"));
      expect(await subnet.balanceOf(user1.address)).to.equal(ethers.parseEther("100"));
    });

    it("approves and transferFrom", async function () {
      await subnet.approve(user1.address, ethers.parseEther("100"));
      await subnet.connect(user1).transferFrom(owner.address, user1.address, ethers.parseEther("100"));
      expect(await subnet.balanceOf(user1.address)).to.equal(ethers.parseEther("100"));
    });
  });
});
