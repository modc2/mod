// SPDX-License-Identifier: MIT
// NFT — an ERC721 collection with sequential ids and a shared base URI.
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract MyNFT is ERC721, Ownable {
    uint256 public nextId;
    string private baseURI;

    constructor(string memory name_, string memory symbol_, string memory baseURI_)
        ERC721(name_, symbol_)
    {
        baseURI = baseURI_;
    }

    function mint(address to) external onlyOwner returns (uint256 id) {
        id = nextId++;
        _safeMint(to, id);
    }

    function setBaseURI(string calldata baseURI_) external onlyOwner {
        baseURI = baseURI_;
    }

    function _baseURI() internal view override returns (string memory) {
        return baseURI;
    }
}
