const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("MyNFT", function () {
  let nft, owner, user;
  const BASE = "ipfs://QmBase/";

  beforeEach(async function () {
    [owner, user] = await ethers.getSigners();
    nft = await (await ethers.getContractFactory("MyNFT"))
      .deploy("My Collection", "MYC", BASE);
    await nft.waitForDeployment();
  });

  it("mints sequential ids to whoever the owner picks", async function () {
    await nft.mint(user.address);
    await nft.mint(user.address);
    expect(await nft.ownerOf(0)).to.equal(user.address);
    expect(await nft.ownerOf(1)).to.equal(user.address);
    expect(await nft.balanceOf(user.address)).to.equal(2n);
    expect(await nft.nextId()).to.equal(2n);
  });

  it("serves tokenURI off the base URI", async function () {
    await nft.mint(user.address);
    expect(await nft.tokenURI(0)).to.equal(`${BASE}0`);
    await nft.setBaseURI("https://cdn.example/");
    expect(await nft.tokenURI(0)).to.equal("https://cdn.example/0");
  });

  it("keeps minting to the owner", async function () {
    await expect(nft.connect(user).mint(user.address))
      .to.be.revertedWith("Ownable: caller is not the owner");
  });
});
